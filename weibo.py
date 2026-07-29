import json
import mimetypes
import os
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple
from urllib.parse import parse_qs, unquote, urljoin, urlparse

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


MOBILE_USER_AGENT = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 "
    "Mobile/15E148 Safari/604.1"
)
DESKTOP_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/138.0.0.0 Safari/537.36"
)
DEFAULT_HEADERS = {
    "User-Agent": MOBILE_USER_AGENT,
    "Accept": "application/json, text/plain, */*",
    "Referer": "https://m.weibo.cn/",
}
STATUS_ENDPOINT = "https://m.weibo.cn/statuses/show"
DESKTOP_STATUS_ENDPOINT = "https://weibo.com/ajax/statuses/show"
VISITOR_ENDPOINT = "https://visitor.passport.weibo.cn/visitor/genvisitor2"
WEIBO_HOSTS = {
    "weibo.com",
    "www.weibo.com",
    "m.weibo.cn",
    "www.weibo.cn",
}
SHORT_LINK_HOSTS = {"t.cn", "www.t.cn"}
IMAGE_SIZE_SEGMENTS = {
    "bmiddle",
    "large",
    "mw1024",
    "mw2000",
    "orj360",
    "orj480",
    "orj960",
    "original",
    "small",
    "square",
    "thumbnail",
    "wap180",
}


class WeiboError(RuntimeError):
    """Base error for a failed Weibo request or download."""


class GuestSessionError(WeiboError):
    """Raised when Weibo's anonymous visitor session cannot be created."""


class PostUnavailableError(WeiboError):
    """Raised when a post is deleted, private, paid, or otherwise unavailable."""


def _retry_adapter() -> HTTPAdapter:
    retry = Retry(
        total=4,
        connect=4,
        read=4,
        backoff_factor=0.5,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=frozenset(("GET", "HEAD", "POST")),
        raise_on_status=False,
    )
    return HTTPAdapter(max_retries=retry)


def create_session() -> requests.Session:
    session = requests.Session()
    session.headers.update(DEFAULT_HEADERS)
    adapter = _retry_adapter()
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session


def extract_redirected_link(
    short_link: str, session: Optional[requests.Session] = None
) -> str:
    """Resolve a t.cn link without requiring a logged-in account."""
    client = session or create_session()
    current_url = short_link
    for _ in range(5):
        try:
            response = client.get(
                current_url,
                allow_redirects=False,
                timeout=(8, 20),
            )
            response.raise_for_status()
        except requests.RequestException as exc:
            raise WeiboError("无法解析微博短链：{}".format(exc)) from exc

        location = response.headers.get("Location")
        if not location:
            return response.url or current_url
        next_url = urljoin(current_url, location)
        next_host = urlparse(next_url).netloc.lower().split(":", 1)[0]
        if next_host not in SHORT_LINK_HOSTS:
            return next_url
        current_url = next_url
    raise WeiboError("微博短链重定向次数过多。")


def _post_id_from_url(url: str) -> Optional[str]:
    parsed = urlparse(url)
    host = parsed.netloc.lower().split(":", 1)[0]
    if host not in WEIBO_HOSTS:
        return None

    path_parts = [unquote(part) for part in parsed.path.split("/") if part]
    if not path_parts:
        return None

    if path_parts[0] in {"status", "detail"} and len(path_parts) >= 2:
        candidate = path_parts[1]
    elif path_parts[0] == "u":
        candidate = None
    elif len(path_parts) >= 2 and path_parts[0].isdigit():
        candidate = path_parts[1]
    else:
        candidate = None

    if candidate and re.fullmatch(r"[A-Za-z0-9]+", candidate):
        return candidate

    query = parse_qs(parsed.query)
    for key in ("id", "mid"):
        values = query.get(key, [])
        if values and re.fullmatch(r"[A-Za-z0-9]+", values[0]):
            return values[0]
    return None


def get_page_id(
    url: str, session: Optional[requests.Session] = None
) -> Optional[str]:
    """Extract the numeric MID or base62 BID accepted by the mobile API."""
    url = url.strip()
    parsed = urlparse(url)
    host = parsed.netloc.lower().split(":", 1)[0]
    if host in SHORT_LINK_HOSTS:
        url = extract_redirected_link(url, session=session)
    return _post_id_from_url(url)


def _extract_jsonp_object(text: str) -> Dict:
    match = re.search(r"\((\{.*\})\)\s*;?\s*$", text, re.DOTALL)
    if not match:
        raise GuestSessionError("微博访客接口返回了无法识别的数据。")
    try:
        return json.loads(match.group(1))
    except json.JSONDecodeError as exc:
        raise GuestSessionError("微博访客接口返回了无效 JSON。") from exc


def _bootstrap_guest_session(
    session: requests.Session, visitor_html: str, return_url: str
) -> None:
    request_id_match = re.search(
        r"""(?:var\s+)?request_id\s*=\s*["']([^"']+)["']""", visitor_html
    )
    version_match = re.search(
        r"""genvisitor2[^"']*["']?\s*,?\s*["'][^"']*ver=(\d+)""",
        visitor_html,
    )
    if not version_match:
        version_match = re.search(r"""ver=(\d{8})""", visitor_html)

    if not request_id_match:
        raise GuestSessionError("微博匿名访客页面缺少 request_id，接口形式可能已改变。")

    payload = {
        "cb": "visitor_gray_callback",
        "ver": version_match.group(1) if version_match else "20250916",
        "request_id": request_id_match.group(1),
        "tid": "",
        "from": "weibo",
        "webdriver": "false",
        "rid": str(int(time.time() * 1000)),
        "return_url": return_url,
    }
    headers = {
        "Content-Type": "application/x-www-form-urlencoded",
        "Referer": "https://visitor.passport.weibo.cn/",
    }
    try:
        response = session.post(
            VISITOR_ENDPOINT,
            data=payload,
            headers=headers,
            timeout=(8, 20),
        )
        response.raise_for_status()
    except requests.RequestException as exc:
        raise GuestSessionError("无法创建微博匿名访客会话：{}".format(exc)) from exc

    result = _extract_jsonp_object(response.text)
    if result.get("retcode") != 20000000:
        raise GuestSessionError(
            "微博拒绝创建匿名访客会话：{}".format(
                result.get("msg") or result.get("retcode")
            )
        )
    if not session.cookies.get("SUB", domain=".weibo.cn"):
        data = result.get("data") or {}
        if data.get("sub"):
            session.cookies.set("SUB", data["sub"], domain=".weibo.cn", path="/")
        if data.get("subp"):
            session.cookies.set("SUBP", data["subp"], domain=".weibo.cn", path="/")


def _decode_status_response(response: requests.Response) -> Optional[Dict]:
    content_type = response.headers.get("Content-Type", "")
    if "json" not in content_type.lower() and not response.text.lstrip().startswith("{"):
        return None
    try:
        payload = response.json()
    except ValueError:
        return None
    if payload.get("ok") == 1 and isinstance(payload.get("data"), dict):
        return payload["data"]
    message = payload.get("msg") or payload.get("message")
    if message:
        raise PostUnavailableError("微博内容不可用：{}".format(message))
    return None


def _guest_cookie_header(session: requests.Session) -> str:
    values = {}
    for cookie in session.cookies:
        if cookie.name in {"SUB", "SUBP"} and cookie.domain.endswith(".weibo.cn"):
            values[cookie.name] = cookie.value
    return "; ".join(
        "{}={}".format(name, values[name])
        for name in ("SUB", "SUBP")
        if values.get(name)
    )


def _fetch_desktop_status(
    page_id: str, session: requests.Session
) -> Optional[Dict]:
    """Use the guest cookie on the richer desktop endpoint when available."""
    cookie_header = _guest_cookie_header(session)
    if not cookie_header:
        return None
    headers = {
        "User-Agent": DESKTOP_USER_AGENT,
        "Accept": "application/json, text/plain, */*",
        "Referer": "https://weibo.com/",
        "Cookie": cookie_header,
    }
    try:
        response = session.get(
            DESKTOP_STATUS_ENDPOINT,
            params={"id": page_id},
            headers=headers,
            allow_redirects=False,
            timeout=(8, 25),
        )
        response.raise_for_status()
        payload = response.json()
    except (requests.RequestException, ValueError):
        return None
    if isinstance(payload, dict) and payload.get("user") and (
        payload.get("id") or payload.get("mid")
    ):
        return payload
    return None


def _merge_media_lists(preferred: List, alternate: List) -> List:
    merged = []
    fingerprints = set()
    for item in preferred + alternate:
        try:
            fingerprint = json.dumps(item, sort_keys=True, ensure_ascii=False)
        except (TypeError, ValueError):
            fingerprint = repr(item)
        if fingerprint in fingerprints:
            continue
        fingerprints.add(fingerprint)
        merged.append(item)
    return merged


def _merge_status_payloads(preferred: Dict, alternate: Dict) -> Dict:
    """Merge desktop/mobile metadata so media candidates from neither are lost."""
    merged = dict(alternate)
    for key, preferred_value in preferred.items():
        alternate_value = alternate.get(key)
        if isinstance(preferred_value, dict) and isinstance(alternate_value, dict):
            merged[key] = _merge_status_payloads(preferred_value, alternate_value)
        elif (
            isinstance(preferred_value, list)
            and isinstance(alternate_value, list)
            and key in {"items", "pic_ids", "pics", "playback_list"}
        ):
            merged[key] = _merge_media_lists(preferred_value, alternate_value)
        else:
            merged[key] = preferred_value
    return merged


def weibo_pagesource(
    page_id: str, session: Optional[requests.Session] = None
) -> Dict:
    """Fetch a public post through Weibo's anonymous mobile visitor flow."""
    if not page_id:
        raise WeiboError("微博链接中没有可识别的微博 ID。")

    client = session or create_session()
    params = {"id": page_id}
    return_url = "{}?id={}".format(STATUS_ENDPOINT, page_id)

    try:
        response = client.get(
            STATUS_ENDPOINT,
            params=params,
            allow_redirects=True,
            timeout=(8, 25),
        )
        response.raise_for_status()
    except requests.RequestException as exc:
        raise WeiboError("获取微博详情失败：{}".format(exc)) from exc

    mobile_data = _decode_status_response(response)
    if mobile_data is None:
        if (
            "visitor_gray" not in response.text
            and "Sina Visitor System" not in response.text
        ):
            if "内容暂不可见" in response.text:
                raise PostUnavailableError("该微博目前不可见、已删除或受访问权限限制。")
            raise WeiboError("微博详情接口没有返回 JSON，且不是可识别的访客页面。")

        _bootstrap_guest_session(client, response.text, return_url)
        try:
            response = client.get(
                STATUS_ENDPOINT,
                params=params,
                allow_redirects=False,
                timeout=(8, 25),
            )
            response.raise_for_status()
        except requests.RequestException as exc:
            raise WeiboError(
                "匿名访客会话建立后获取微博详情失败：{}".format(exc)
            ) from exc

        mobile_data = _decode_status_response(response)
        if mobile_data is None:
            if "内容暂不可见" in response.text:
                raise PostUnavailableError("该微博目前不可见、已删除或受访问权限限制。")
            raise PostUnavailableError("匿名访客无法读取该微博，内容可能已删除或受限。")

    desktop_data = _fetch_desktop_status(page_id, client)
    if desktop_data:
        return _merge_status_payloads(desktop_data, mobile_data)
    return mobile_data


def get_page_type(response: Dict) -> str:
    if response.get("mix_media_info", {}).get("items"):
        return "multimedia"
    if _find_video_info(response):
        return "video"
    if response.get("pics") or response.get("pic_infos") or response.get("pic_ids"):
        return "images"
    if response.get("retweeted_status"):
        return get_page_type(response["retweeted_status"])
    return "unknown"


def get_pic_type(response: Dict, pic_id: str) -> str:
    return response.get("pic_infos", {}).get(pic_id, {}).get("type", "pic")


def _normalize_original_image_url(url: str) -> str:
    if not url:
        return url
    parsed = urlparse(url)
    path_parts = parsed.path.split("/")
    if len(path_parts) >= 3 and path_parts[-2].lower() in IMAGE_SIZE_SEGMENTS:
        path_parts[-2] = "large"
        path = "/".join(path_parts)
        return parsed._replace(path=path).geturl()
    return url


def _extension_from_url(url: str, default: str) -> str:
    suffix = Path(unquote(urlparse(url).path)).suffix.lower()
    if re.fullmatch(r"\.[a-z0-9]{2,5}", suffix):
        return suffix
    return default


def _image_media(pic: Dict, fallback_id: Optional[str] = None) -> List[Dict]:
    pic_id = str(pic.get("pid") or pic.get("pic_id") or fallback_id or "image")
    largest = pic.get("largest") or pic.get("large") or {}
    if isinstance(largest, str):
        largest_url = largest
    else:
        largest_url = largest.get("url")
    image_url = (
        largest_url
        or pic.get("original_pic")
        or pic.get("url")
        or pic.get("bmiddle_pic")
    )
    media = []
    if image_url:
        image_url = _normalize_original_image_url(image_url)
        media.append(
            {
                "url": image_url,
                "media_id": pic_id,
                "media_type": "pic",
                "extension": _extension_from_url(image_url, ".jpg"),
            }
        )

    live_url = (
        pic.get("video")
        or pic.get("video_url")
        or pic.get("livephoto_url")
        or pic.get("motion_url")
    )
    if live_url:
        media.append(
            {
                "url": live_url,
                "media_id": "{}_live".format(pic_id),
                "media_type": "livephoto",
                "extension": _extension_from_url(live_url, ".mov"),
            }
        )
    return media


def _parse_video_dimensions(value: str) -> Tuple[int, int]:
    matches = re.findall(r"(?<!\d)(\d{3,4})[xX](\d{3,4})(?!\d)", value or "")
    if matches:
        width, height = matches[-1]
        return int(width), int(height)
    return 0, 0


def _quality_hint(value: str) -> int:
    value = (value or "").lower()
    hints = (
        ("8k", 4320),
        ("4k", 2160),
        ("2160", 2160),
        ("2k", 1440),
        ("1440", 1440),
        ("1080", 1080),
        ("720", 720),
        ("540", 540),
        ("480", 480),
        ("360", 360),
        ("hd", 540),
        ("ld", 360),
    )
    for token, height in hints:
        if token in value:
            return height
    return 0


def _candidate_score(candidate: Dict) -> Tuple[int, int, int, int]:
    key = str(candidate.get("key") or "")
    url = str(candidate.get("url") or "")
    descriptor = "{} {}".format(key, url)
    width = int(candidate.get("width") or 0)
    height = int(candidate.get("height") or 0)
    parsed_width, parsed_height = _parse_video_dimensions(descriptor)
    width = max(width, parsed_width)
    height = max(height, parsed_height)
    hinted_height = _quality_hint(descriptor)
    long_edge = max(width, height, hinted_height)
    short_edge = min(width, height) if width and height else 0
    resolution = max(short_edge, hinted_height)
    bitrate = int(float(candidate.get("bitrate") or 0))
    fps = int(
        float(candidate.get("fps") or (60 if "60fps" in descriptor.lower() else 0))
    )
    return resolution, long_edge, fps, bitrate


def _is_direct_video_url(url: str) -> bool:
    lower_url = (url or "").lower()
    host = urlparse(url or "").netloc.lower()
    return (
        ".mp4" in lower_url
        or ",video" in lower_url
        or host == "video.weibocdn.com"
        or host.endswith(".video.weibocdn.com")
    )


def _video_candidates(media_info: Dict, urls: Optional[Dict] = None) -> List[Dict]:
    candidates = []
    seen = set()

    def add(url: Optional[str], key: str = "", details: Optional[Dict] = None) -> None:
        if not url or not isinstance(url, str) or url in seen:
            return
        seen.add(url)
        details = details or {}
        candidates.append(
            {
                "url": url,
                "key": key,
                "width": details.get("width") or details.get("video_width") or 0,
                "height": details.get("height") or details.get("video_height") or 0,
                "bitrate": details.get("bitrate") or details.get("video_bitrate") or 0,
                "fps": details.get("fps") or details.get("frame_rate") or 0,
            }
        )

    for mapping in (urls or {}, media_info):
        if not isinstance(mapping, dict):
            continue
        for key, value in mapping.items():
            if not isinstance(value, str):
                continue
            if _is_direct_video_url(value):
                add(value, key)

    for item in media_info.get("playback_list") or []:
        play_info = item.get("play_info") or item
        playback_url = play_info.get("url")
        mime = str(play_info.get("mime") or "")
        if (mime and not mime.startswith("video/")) or not _is_direct_video_url(
            playback_url
        ):
            continue
        add(
            playback_url,
            str(
                play_info.get("quality_desc")
                or play_info.get("quality_label")
                or item.get("type")
                or "playback"
            ),
            play_info,
        )
    return candidates


def _find_video_info(response: Dict) -> Optional[Tuple[Dict, Dict, Dict]]:
    page_info = response.get("page_info") or {}
    media_info = page_info.get("media_info") or {}
    urls = page_info.get("urls") or media_info.get("urls") or {}
    if _video_candidates(media_info, urls):
        return page_info, media_info, urls
    return None


def _video_media(response: Dict) -> List[Dict]:
    found = _find_video_info(response)
    if not found:
        return []
    page_info, media_info, urls = found
    candidates = _video_candidates(media_info, urls)
    best = max(candidates, key=_candidate_score)
    media_id = str(
        media_info.get("media_id")
        or page_info.get("object_id")
        or response.get("fid")
        or response.get("id")
        or "video"
    )
    return [
        {
            "url": best["url"],
            "media_id": media_id,
            "media_type": "video",
            "extension": _extension_from_url(best["url"], ".mp4"),
            "quality": _candidate_score(best),
        }
    ]


def _direct_media(response: Dict) -> List[Dict]:
    media = []
    mix_items = response.get("mix_media_info", {}).get("items") or []
    if mix_items:
        for item in mix_items:
            data = item.get("data") or {}
            if item.get("type") == "pic":
                media.extend(_image_media(data))
            elif item.get("type") == "video":
                wrapper = {
                    "id": response.get("id"),
                    "page_info": {
                        "object_id": data.get("object_id"),
                        "media_info": data.get("media_info") or {},
                        "urls": data.get("urls") or {},
                    },
                }
                media.extend(_video_media(wrapper))
        return media

    pics = response.get("pics") or []
    for pic in pics:
        media.extend(_image_media(pic))

    pic_infos = response.get("pic_infos") or {}
    if not pics and pic_infos:
        for pic_id in response.get("pic_ids") or pic_infos.keys():
            if pic_id in pic_infos:
                media.extend(_image_media(pic_infos[pic_id], str(pic_id)))

    if not pics and not pic_infos and response.get("original_pic"):
        media.extend(
            _image_media(
                {
                    "pid": (response.get("pic_ids") or [response.get("id")])[0],
                    "original_pic": response["original_pic"],
                }
            )
        )

    media.extend(_video_media(response))
    return media


def _deduplicate_media(media: Iterable[Dict]) -> List[Dict]:
    result = []
    seen = set()
    for item in media:
        key = (item.get("media_type"), item.get("media_id"), item.get("url"))
        if not item.get("url") or key in seen:
            continue
        seen.add(key)
        result.append(item)
    return result


def get_media_urls(response: Dict, page_type: Optional[str] = None) -> List[Dict]:
    """Extract original images and the highest-quality public video rendition."""
    del page_type  # Kept for compatibility with the previous public function.
    media = _direct_media(response)
    if not media and isinstance(response.get("retweeted_status"), dict):
        media = _direct_media(response["retweeted_status"])
    return _deduplicate_media(media)


def get_user_info(response: Dict) -> Dict[str, str]:
    source = response
    if not source.get("user") and isinstance(source.get("retweeted_status"), dict):
        source = source["retweeted_status"]
    user = source.get("user") or {}
    if not user.get("id") or not user.get("screen_name"):
        raise WeiboError("微博详情中缺少作者信息。")
    return {"screen_name": str(user["screen_name"]), "uid": str(user["id"])}


def _safe_path_component(value: str, fallback: str) -> str:
    value = re.sub(r"[\x00-\x1f<>:\"/\\|?*]+", "_", value).strip(" .")
    return value or fallback


def _download_headers() -> Dict[str, str]:
    return {
        "User-Agent": MOBILE_USER_AGENT,
        "Accept": "*/*",
        "Referer": "https://weibo.com/",
    }


def download_media(url: str, file_path: str, uid: str = "") -> bool:
    """Stream one media file to disk and atomically publish it when complete."""
    destination = Path(file_path)
    if destination.is_file() and destination.stat().st_size > 0:
        print("已存在，跳过：{}".format(destination))
        return False

    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(destination.name + ".part")
    session = requests.Session()
    session.headers.update(_download_headers())
    session.mount("https://", _retry_adapter())
    session.mount("http://", _retry_adapter())

    try:
        with session.get(url, stream=True, timeout=(10, 60)) as response:
            response.raise_for_status()
            content_type = response.headers.get("Content-Type", "").lower()
            if "text/html" in content_type or "application/json" in content_type:
                raise WeiboError(
                    "媒体地址返回了 {}，可能已过期或被限制。".format(content_type)
                )
            with temporary.open("wb") as output:
                for chunk in response.iter_content(chunk_size=1024 * 256):
                    if chunk:
                        output.write(chunk)
        if not temporary.exists() or temporary.stat().st_size == 0:
            raise WeiboError("下载结果为空。")
        os.replace(str(temporary), str(destination))
        print("已保存：{}".format(destination))
        return True
    except Exception as exc:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass
        error_file = destination.parent / "not_downloaded.txt"
        with error_file.open("a", encoding="utf-8") as output:
            output.write("{}:{}:{}\n".format(uid, destination, url))
        raise WeiboError("下载 {} 失败：{}".format(destination.name, exc)) from exc
    finally:
        session.close()


def _media_filename(media: Dict) -> str:
    media_id = _safe_path_component(str(media.get("media_id") or "media"), "media")
    extension = str(media.get("extension") or "")
    if not extension.startswith("."):
        extension = "." + extension
    if extension == ".":
        guessed = mimetypes.guess_extension(media.get("content_type") or "")
        extension = guessed or {
            "pic": ".jpg",
            "video": ".mp4",
            "livephoto": ".mov",
        }.get(media.get("media_type"), ".bin")
    return media_id + extension


def weibo_image_download(url: str, save_folder: str = "images") -> List[str]:
    """Download all public original images, Live Photos, and best-quality videos."""
    print("正在解析：{}".format(url))
    session = create_session()
    try:
        page_id = get_page_id(url, session=session)
        if not page_id:
            raise WeiboError("无法从链接中识别微博 ID：{}".format(url))
        response = weibo_pagesource(page_id, session=session)
    finally:
        session.close()

    media_urls = get_media_urls(response)
    if not media_urls:
        if response.get("is_paid"):
            raise PostUnavailableError("该微博媒体需要付费，匿名访客不能下载。")
        raise PostUnavailableError("该微博没有可供匿名访客下载的图片或视频。")

    user_info = get_user_info(response)
    user_folder = "{}_{}".format(
        _safe_path_component(user_info["screen_name"], "weibo_user"),
        _safe_path_component(user_info["uid"], "unknown"),
    )
    destination = Path(save_folder) / user_folder
    destination.mkdir(parents=True, exist_ok=True)

    completed = []
    failures = []
    workers = min(8, len(media_urls))
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {}
        for media in media_urls:
            file_path = destination / _media_filename(media)
            future = executor.submit(
                download_media,
                media["url"],
                str(file_path),
                user_info["uid"],
            )
            futures[future] = file_path

        for future in as_completed(futures):
            file_path = futures[future]
            try:
                future.result()
                completed.append(str(file_path))
            except Exception as exc:
                failures.append(str(exc))
                print(str(exc), file=sys.stderr)

    if failures:
        raise WeiboError(
            "{} 个媒体下载失败；{} 个已成功。".format(len(failures), len(completed))
        )
    print("下载完成：{}（{} 个媒体）".format(user_info["screen_name"], len(completed)))
    return completed
