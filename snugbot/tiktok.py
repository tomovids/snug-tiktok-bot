"""TikTok Login Kit + Content Posting API: photo slideshow to the creator's drafts (MEDIA_UPLOAD)."""
from __future__ import annotations

import time
import urllib.parse

import requests

AUTH_URL = "https://www.tiktok.com/v2/auth/authorize/"
TOKEN_URL = "https://open.tiktokapis.com/v2/oauth/token/"
USER_INFO_URL = "https://open.tiktokapis.com/v2/user/info/"
VIDEO_LIST_URL = "https://open.tiktokapis.com/v2/video/list/"
VIDEO_FIELDS = ("id,create_time,title,video_description,view_count,like_count,comment_count,"
                "share_count,share_url,cover_image_url,duration")
INIT_URL = "https://open.tiktokapis.com/v2/post/publish/content/init/"
STATUS_URL = "https://open.tiktokapis.com/v2/post/publish/status/fetch/"

SENT = "SEND_TO_USER_INBOX"
POSTED = "PUBLISH_COMPLETE"

SETUP = "See SETUP.md."
HINTS = {
    "spam_risk_too_many_pending_share":
        "TikTok allows at most 5 waiting drafts in 24 hours. Open TikTok, post or delete the drafts "
        "in your inbox, then run the workflow again.",
    "scope_not_authorized":
        "The TikTok login is missing a permission (video.upload for drafts; user.info.stats and video.list "
        "for the dashboard). Check the scopes on your TikTok app, then run `python -m snugbot authorize` "
        "again. " + SETUP,
    "access_token_invalid":
        "The TikTok login has expired or was removed. Run `python -m snugbot authorize` again.",
    "invalid_grant":
        "The TikTok login has expired or was removed. Run `python -m snugbot authorize` again.",
    "auth_removed":
        "Access was removed in the TikTok app. Run `python -m snugbot authorize` again.",
    "url_ownership_unverified":
        "TikTok hasn't verified the image address. Verify the URL prefix in the TikTok developer "
        "portal (SETUP.md step 4).",
    "unaudited_client_can_only_post_to_private_accounts":
        "TikTok only lets unreviewed apps post to private accounts. See 'If TikTok blocks drafts' in SETUP.md.",
    "app_version_check_failed":
        "Update the TikTok app on your phone (version 31.8 or newer is needed for drafts).",
    "rate_limit_exceeded": "TikTok's rate limit was hit. Run the workflow again in a few minutes.",
    "photo_pull_failed":
        "TikTok couldn't download the slides from GitHub Pages. Run the workflow again; if it keeps "
        "happening, check the Pages site is up.",
    "picture_size_check_failed": "TikTok rejected a slide's size (max 1080x1920).",
    "file_format_check_failed": "TikTok rejected a slide's format (JPEG/WebP only).",
    "spam_risk_too_many_posts": "TikTok's daily post limit for this account was reached.",
    "spam_risk_user_banned_from_posting": "TikTok has blocked this account from posting.",
}


class TikTokError(Exception):
    def __init__(self, code: str, message: str = "", log_id: str = ""):
        self.code = code
        self.hint = HINTS.get(code, "")
        text = f"TikTok error {code}"
        if message:
            text += f": {message}"
        if log_id:
            text += f" (log id {log_id})"
        if self.hint:
            text += f"\n{self.hint}"
        super().__init__(text)


class TikTok:
    def __init__(self, client_key: str, client_secret: str, session: requests.Session | None = None,
                 sleep=time.sleep, now=time.time):
        self.key = client_key
        self.secret = client_secret
        self.http = session or requests.Session()
        self.sleep = sleep
        self.now = now

    # ---- login ----------------------------------------------------------------------------

    def authorize_url(self, redirect_uri: str, scopes: list[str], state: str) -> str:
        q = {"client_key": self.key, "scope": ",".join(scopes), "response_type": "code",
             "redirect_uri": redirect_uri, "state": state}
        return AUTH_URL + "?" + urllib.parse.urlencode(q)

    def _token(self, form: dict) -> dict:
        r = self.http.post(TOKEN_URL, data={"client_key": self.key, "client_secret": self.secret, **form},
                           headers={"Content-Type": "application/x-www-form-urlencoded"}, timeout=30)
        try:
            data = r.json()
        except ValueError:
            raise TikTokError(f"http_{r.status_code}", r.text[:300])
        if "access_token" not in data:
            raise TikTokError(data.get("error", f"http_{r.status_code}"),
                              data.get("error_description", ""), data.get("log_id", ""))
        t = self.now()
        return {
            "access_token": data["access_token"],
            "refresh_token": data["refresh_token"],
            "open_id": data.get("open_id", ""),
            "scope": data.get("scope", ""),
            "expires_at": t + int(data.get("expires_in", 0)),
            "refresh_expires_at": t + int(data.get("refresh_expires_in", 0)),
        }

    def exchange_code(self, code: str, redirect_uri: str) -> dict:
        return self._token({"grant_type": "authorization_code", "code": code.strip(),
                            "redirect_uri": redirect_uri})

    def refresh(self, tokens: dict) -> dict:
        return self._token({"grant_type": "refresh_token", "refresh_token": tokens["refresh_token"]})

    def account_stats(self, access_token: str) -> dict:
        """Followers, total likes and post count (user.info.stats)."""
        r = self.http.get(USER_INFO_URL, timeout=30, headers={"Authorization": f"Bearer {access_token}"},
                          params={"fields": "open_id,display_name,follower_count,following_count,"
                                            "likes_count,video_count"})
        return self._read(r).get("user", {})

    def videos(self, access_token: str, max_pages: int = 10) -> list[dict]:
        """The account's public posts, newest first (video.list), up to max_pages x 20."""
        out, cursor = [], None
        for _ in range(max_pages):
            body = {"max_count": 20}
            if cursor:
                body["cursor"] = cursor
            r = self.http.post(VIDEO_LIST_URL, params={"fields": VIDEO_FIELDS}, json=body, timeout=30,
                               headers={"Authorization": f"Bearer {access_token}",
                                        "Content-Type": "application/json; charset=UTF-8"})
            data = self._read(r)
            out += data.get("videos", [])
            if not data.get("has_more"):
                break
            cursor = data.get("cursor")
        return out

    @staticmethod
    def _read(r) -> dict:
        try:
            data = r.json()
        except ValueError:
            raise TikTokError(f"http_{r.status_code}", r.text[:300])
        err = data.get("error") or {}
        if err.get("code", "ok") != "ok":
            raise TikTokError(err.get("code"), err.get("message", ""), err.get("log_id", ""))
        return data.get("data") or {}

    def display_name(self, access_token: str) -> str:
        """The connected account's display name (user.info.basic), to confirm the right account."""
        r = self.http.get(USER_INFO_URL, params={"fields": "open_id,display_name"}, timeout=30,
                          headers={"Authorization": f"Bearer {access_token}"})
        try:
            data = r.json()
        except ValueError:
            raise TikTokError(f"http_{r.status_code}", r.text[:300])
        err = data.get("error") or {}
        if err.get("code", "ok") != "ok":
            raise TikTokError(err.get("code"), err.get("message", ""), err.get("log_id", ""))
        return (data.get("data") or {}).get("user", {}).get("display_name", "")

    # ---- posting --------------------------------------------------------------------------

    def _api(self, url: str, access_token: str, body: dict, tries: int = 3) -> dict:
        last: TikTokError | None = None
        for attempt in range(tries):
            try:
                r = self.http.post(url, json=body, timeout=60, headers={
                    "Authorization": f"Bearer {access_token}",
                    "Content-Type": "application/json; charset=UTF-8"})
            except requests.RequestException as e:
                last = TikTokError("network_error", str(e))
            else:
                try:
                    data = r.json()
                except ValueError:
                    data = {"error": {"code": f"http_{r.status_code}", "message": r.text[:300]}}
                err = data.get("error") or {}
                code = err.get("code", "ok" if r.status_code == 200 else f"http_{r.status_code}")
                if code == "ok":
                    return data.get("data") or {}
                last = TikTokError(code, err.get("message", ""), err.get("log_id", ""))
                if not (r.status_code == 429 or r.status_code >= 500):
                    raise last
            if attempt < tries - 1:
                self.sleep(5 * (attempt + 1))
        raise last  # type: ignore[misc]

    def send_photo_draft(self, access_token: str, urls: list[str], title: str, description: str,
                         ai_generated: bool, log=print) -> str:
        body = {
            "media_type": "PHOTO",
            "post_mode": "MEDIA_UPLOAD",
            "post_info": {"title": title, "description": description},
            "source_info": {"source": "PULL_FROM_URL", "photo_images": urls, "photo_cover_index": 0},
        }
        if ai_generated:
            body["is_aigc"] = True
        try:
            data = self._api(INIT_URL, access_token, body)
        except TikTokError as e:
            if e.code != "invalid_param" or "is_aigc" not in body:
                raise
            log(f"TikTok refused the AI label for drafts ({e}); sending without it.")
            body.pop("is_aigc")
            data = self._api(INIT_URL, access_token, body)
        return data["publish_id"]

    def status(self, access_token: str, publish_id: str) -> dict:
        return self._api(STATUS_URL, access_token, {"publish_id": publish_id})

    def wait_until_sent(self, access_token: str, publish_id: str, timeout: float = 600,
                        interval: float = 10, log=print) -> str:
        start = self.now()
        last = ""
        while True:
            data = self.status(access_token, publish_id)
            state = data.get("status", "")
            if state != last:
                log(f"TikTok status: {state}")
                last = state
            if state in (SENT, POSTED):
                return state
            if state == "FAILED":
                raise TikTokError(data.get("fail_reason") or "failed", "TikTok could not make the draft")
            if self.now() - start > timeout:
                raise TikTokError("timeout", f"still '{state}' after {int(timeout)} s")
            self.sleep(interval)


def wait_for_urls(urls: list[str], timeout: float = 600, interval: float = 10,
                  session: requests.Session | None = None, sleep=time.sleep, now=time.time,
                  log=print) -> None:
    """Waits until GitHub Pages serves every slide (200 with an image content type)."""
    http = session or requests.Session()
    start = now()
    while True:
        missing = []
        for u in urls:
            try:
                r = http.get(u, timeout=30, stream=True, allow_redirects=False)
                ok = r.status_code == 200 and r.headers.get("Content-Type", "").startswith("image/")
                r.close()
            except requests.RequestException:
                ok = False
            if not ok:
                missing.append(u)
        if not missing:
            return
        if now() - start > timeout:
            raise TikTokError("pages_not_ready", f"GitHub Pages isn't serving: {', '.join(missing)}")
        log(f"Waiting for GitHub Pages ({len(missing)} slide(s) not live yet)...")
        sleep(interval)
