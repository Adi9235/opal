import hashlib
import hmac
import re
from typing import List, Optional

from fastapi import Header, HTTPException, Request, status
from opal_common.schemas.webhook import SecretTypeEnum
from opal_server.config import opal_server_config
from pydantic import BaseModel


async def validate_git_secret_or_throw(request: Request) -> bool:
    """authenticates a request from a git service webhook system by checking
    that the request contains a valid signature (i.e: via the secret stored on
    github) or a valid token (as stored in Gitlab)."""
    if opal_server_config.POLICY_REPO_WEBHOOK_SECRET is None:
        # webhook can be configured without secret (not recommended but quite possible)
        return True

    # get the secret the git service has sent us
    secret = request.headers.get(
        opal_server_config.POLICY_REPO_WEBHOOK_PARAMS.secret_header_name, ""
    )

    # parse out the actual secret (Some services like Github add prefixes)
    matches = re.findall(
        opal_server_config.POLICY_REPO_WEBHOOK_PARAMS.secret_parsing_regex,
        secret,
    )
    secret = matches[1] if len(matches) > 0 else None

    # check we actually got something
    if secret is None or len(secret) == 0:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="No secret was provided!",
        )

    # Check secret as signature
    if (
        opal_server_config.POLICY_REPO_WEBHOOK_PARAMS.secret_type
        == SecretTypeEnum.signature
    ):
        # calculate our signature on the post body
        payload = await request.body()
        our_signature = hmac.new(
            opal_server_config.POLICY_REPO_WEBHOOK_SECRET.encode("utf-8"),
            payload,
            hashlib.sha256,
        ).hexdigest()

        # compare signatures on the post body
        provided_signature = secret
        if not hmac.compare_digest(our_signature, provided_signature):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="signatures didn't match!",
            )
    # Check secret as token
    elif secret != opal_server_config.POLICY_REPO_WEBHOOK_SECRET.encode("utf-8"):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="secret-tokens didn't match!",
        )

    return True


class GitChanges(BaseModel):
    """The summary of a webhook as the properties of what has changed on the
    reporting Git repo.

    urls - the affected repo URLS
    branch - the branch the event affected
    """

    urls: List[str] = []
    branch: Optional[str] = None


async def extracted_git_changes(request: Request) -> GitChanges:
    """extracts the repo url from a webhook request payload.

    used to make sure that the webhook was triggered on *our* monitored
    repo.

    This functions search for common patterns for where the affected URL may appear in the webhook
    """
    payload = await request.json()

    ### --- Get branch ---  ###
    # Gitlab / gitHub style
    ref = payload.get("ref", None)

    # Azure style
    if ref is None:
        ref = payload.get("refUpdates", {}).get("name", None)

    if isinstance(ref, str):
        # remove prefix
        if ref.startswith("refs/heads/"):
            branch = ref[11:]
        else:
            branch = ref
    else:
        branch = None

    ### Get urls ###

    # Github style
    repo_payload = payload.get("repository", {})
    git_url = repo_payload.get("git_url", None)
    ssh_url = repo_payload.get("ssh_url", None)
    clone_url = repo_payload.get("clone_url", None)

    # Gitlab style
    project_payload = payload.get("project", {})
    project_git_http_url = project_payload.get("git_http_url", None)
    project_git_ssh_url = project_payload.get("git_ssh_url", None)

    # Azure style
    resource_payload = payload.get("resource", {})
    azure_repo_payload = resource_payload.get("repository", {})
    remote_url = azure_repo_payload.get("remoteUrl", None)

    # additional support for url payload
    git_http_url = repo_payload.get("git_ssh_url", None)
    ssh_http_url = repo_payload.get("git_http_url", None)
    url = repo_payload.get("url", None)

    # remove duplicates and None
    url_set = set(
        [
            remote_url,
            git_url,
            ssh_url,
            clone_url,
            git_http_url,
            ssh_http_url,
            url,
            project_git_http_url,
            project_git_ssh_url,
        ]
    )
    url_set.discard(None)
    # continue as a list
    urls = list(url_set)
    if not urls:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="repo url not found in payload!",
        )
    return GitChanges(urls=urls, branch=branch)
