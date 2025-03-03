import time
import requests
import json
import re
import os
import sys
from urllib.parse import urlparse
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

class Config:
    def __init__(self, uid = None, cookie= None, save_dir=None):
        # 验证UID是否为数字
        if not str(uid).isdigit():
            raise ValueError("UID 必须为纯数字!")
        self.uid = uid
        
        # 验证Cookie是否非空
        if not cookie.strip():
            raise ValueError("Cookie 不能为空!")
        self.cookie = cookie
        
        # 设置保存路径，默认为当前目录下的weibo_download
        if save_dir is None:
            self.save_dir = os.path.join(os.getcwd(), "weibo_download")
        else:
            self.save_dir = save_dir
    
    def get_uid(self):
        """返回已验证的UID"""
        return self.uid
    
    def get_cookie(self):
        """返回已验证的Cookie"""
        return self.cookie
    
    def get_save_dir(self):
        """返回保存路径"""
        return self.save_dir

class FileManager:
    @staticmethod
    def append_date(file_path, date_str):
        """追加日期字符串到指定文件"""
        with open(file_path, "a", encoding="utf-8") as f:
            f.write(date_str + "\n")
    @staticmethod
    def append_url(file_path, url):
        """追加 URL 到指定文件"""
        with open(file_path, "a", encoding="utf-8") as f:
            f.write(url + "\n")
    @staticmethod
    def update_unsaved_file(file_path, unsaved_set):
        """用未保存的 URL 集合覆盖文件"""
        with open(file_path, "w", encoding="utf-8") as f:
            for url in unsaved_set:
                f.write(url + "\n")
    # 新增：读取文件中的 URL 列表并返回集合
    @staticmethod
    def read_urls(file_path):
        """读取文件中的 URL 列表，返回集合"""
        if not os.path.exists(file_path):
            return set()
        with open(file_path, "r", encoding="utf-8") as f:
            return set(line.strip() for line in f if line.strip())


    @staticmethod
    def update_unsaved_file(file_path, unsaved_set):
        """用未保存的 URL 集合覆盖文件"""
        with open(file_path, "w", encoding="utf-8") as f:
            for url in unsaved_set:
                f.write(url + "\n")

class WeiboUtils:
    @staticmethod
    def clean_content(content):
        """清理HTML内容并保留25个字符"""
        str_length = 25
        # 移除HTML标签
        content = re.sub(r'<[^>]+>', '', content)
        # 替换换行和回车
        content = re.sub(r'[\n\r]', ' ', content)
        # 保留有效字符
        cleaned = re.sub(r'[^\u4e00-\u9fa5a-zA-Z0-9_\s-]', '', content)
        # 压缩空白字符并去除首尾空格
        cleaned = re.sub(r'\s+', ' ', cleaned).strip()
        # 截取前25个字符并确保去除末尾空格
        return cleaned[:str_length].rstrip()

    @staticmethod
    def clean_filename(name):
        """移除文件名中的无效字符"""
        return re.sub(r'[\\/*?:"<>|【】！]', '', name).strip()

    @staticmethod
    def get_publish_time(response):
        """解析并格式化发布时间"""
        try:
            created_at_str = response.get('created_at', '')
            dt = datetime.strptime(created_at_str, '%a %b %d %H:%M:%S %z %Y')
            return dt.strftime('%Y-%m-%d-%H-%M-%S')
        except Exception as e:
            print(f"时间解析失败: {e}，使用当前时间替代")
            return datetime.now().strftime('%Y-%m-%d-%H-%M-%S')

    @staticmethod
    def extract_redirected_link(short_link):
        """从短链接提取重定向 URL"""
        try:
            response = requests.get(short_link, headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36'}, allow_redirects=False)
            if response.status_code == 302 and 'Location' in response.headers:
                return response.headers['Location']
            return None
        except Exception as e:
            print(f"解析短链接失败: {e}")
            return None

    @staticmethod
    def get_page_id(url):
        """从微博 URL 提取页面 ID"""
        if "t.cn" in url:
            url = WeiboUtils.extract_redirected_link(url)
        pattern = re.compile(r'https?://weibo.com/(\d+)/(\w+)')
        match = pattern.match(url)
        return match.group(2) if match else None

class WeiboURLFetcher:
    def __init__(self, uid, cookie, base_dir, interval=3):
        self.uid = uid
        self.cookie = cookie
        self.interval = interval
        self.base_dir = base_dir
        self.username_cache = {}
        self.session = requests.Session()
        #更新Session的headers而不是直接操作Session .headers
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36',
            'Cookie': self.cookie if self.cookie else ''# 避免None导致的异常
        })
        self.containerid = None
        self.username = None
        self.download_dir = None
        self.update_for_uid(uid)

    def get_username(self, uid):
        """通过微博 API 获取用户昵称，支持缓存"""
        if uid in self.username_cache:
            return self.username_cache[uid]
        url = f"https://m.weibo.cn/api/container/getIndex?type=uid&value={uid}"
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36 Edg/122.0.0.0",
            "Cookie": self.cookie
        }
        try:
            time.sleep(3)
            response = requests.get(url, headers=headers, timeout=10)
            if response.status_code == 200:
                data = response.json()
                username = data.get('data', {}).get('userInfo', {}).get('screen_name', f"用户_{uid}")
                self.username_cache[uid] = username
                return username
            else:
                print(f"API 请求失败，状态码: {response.status_code}")
        except Exception as e:
            print(f"获取用户名异常: {e}")
        return f"用户_{uid}"

    def get_download_dir(self, base_dir, uid):
        """获取或创建下载目录，基于用户名和 UID"""
        for subdir in os.listdir(base_dir):
            subdir_path = os.path.join(base_dir, subdir)
            if os.path.isdir(subdir_path):
                parts = subdir.rsplit('_', 1)
                if len(parts) == 2 and parts[1] == uid:
                    self.username_cache[uid] = parts[0]
                    print(f'检测到现有 UID 文件夹: "{subdir}"，跳过 API 获取用户名')
                    return subdir_path
        username = self.get_username(uid)
        new_folder_name = f"{username}_{uid}"
        new_folder_path = os.path.join(base_dir, new_folder_name)
        os.makedirs(new_folder_path, exist_ok=True)
        print(f"创建新文件夹: {new_folder_path}")
        return new_folder_path

    def update_for_uid(self, uid):
        """更新当前处理的 UID 相关属性"""
        self.uid = uid
        self.download_dir = self.get_download_dir(self.base_dir, uid)
        self.username = self.get_username(uid)

    def _get_containerid(self):
        profile_url = f"https://m.weibo.cn/api/container/getIndex?type=uid&value={self.uid}"
        try:
            response = self.session.get(profile_url, timeout=10)
            if response.status_code == 200:
                data = response.json()
                for tab in data.get('data', {}).get('tabsInfo', {}).get('tabs', []):
                    if tab.get('tab_type') == 'weibo':
                        return tab.get('containerid')
        except Exception as e:
            print(f"获取 containerid 失败: {str(e)}")
        return None

    def _parse_weibo_url(self, card):
        if card.get('card_type') != 9 or not card.get('mblog'):
            return None
        mblog = card['mblog']
        user_id = mblog.get('user', {}).get('id')
        bid = mblog.get('bid')
        if user_id and bid:
            return f"https://weibo.com/{user_id}/{bid}"
        return None

    def get_all_urls(self):
        """生成器，按页面返回 URL 列表"""
        self.containerid = self._get_containerid()
        if not self.containerid:
            print("无法获取 containerid，请检查 UID 和 Cookie")
            return
        page = 1
        while True:
            try:
                api_url = f"https://m.weibo.cn/api/container/getIndex?containerid={self.containerid}&page={page}"
                response = self.session.get(api_url, timeout=15)
                if response.status_code != 200:
                    print(f"请求失败，状态码：{response.status_code}")
                    break
                data = response.json()
                cards = data.get('data', {}).get('cards', [])
                page_urls = [self._parse_weibo_url(card) for card in cards if self._parse_weibo_url(card)]
                if not page_urls:
                    break
                yield page_urls
                print(f"第 {page} 页获取到 {len(page_urls)} 条 URL")
                page += 1
                time.sleep(self.interval)
            except Exception as e:
                print(f"获取第 {page} 页数据异常: {str(e)}")
                break
class WeiboDownloader:
    def __init__(self, uid, cookie, base_dir):
        """初始化 WeiboDownloader 类
        
        Args:
            uid (str): 用户ID，用于记录下载失败的媒体
            cookie (str): 用于请求微博API的cookie
            base_dir (str): 下载文件的基础目录
        """
        self.uid = uid
        self.cookie = cookie
        self.base_dir = base_dir
        self.headers = {
            'User_Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36',
        }

    def get_page_id(self, url):
        """从URL中提取页面ID"""
        if "t.cn" in url:
            url = self.extract_redirected_link(url)
        pattern = re.compile(r'https?://weibo.com/(\d+)/?(\w+)?')
        match = pattern.match(url)
        if match:
            return match.group(2) or url[-1]
        return None

    def extract_redirected_link(self, short_link):
        """解析短链接获取真实URL"""
        try:
            response = requests.get(short_link, headers=self.headers, allow_redirects=False)
            if response.status_code == 302:
                return response.headers.get('Location')
            else:
                print(f"Error: Unable to access {short_link}. Status code: {response.status_code}")
                return None
        except Exception as e:
            print(f"Error: {e}")
            return None

    def weibo_pagesource(self, page_id):
        """请求微博API获取页面内容"""
        headers_copy = self.headers.copy()
        headers_copy['Cookie'] = self.cookie
        response = requests.get(f"https://weibo.com/ajax/statuses/show?id={page_id}", headers=headers_copy)
        if response.ok:
            try:
                return response.json()
            except ValueError:
                print("Failed to decode JSON. Response was:", response.text)
        else:
            print("Request failed with status code:", response.status_code)
        return ""

    def get_publish_time(self, response):
        """解析发布时间"""
        try:
            created_at_str = response.get('created_at', '')
            if not created_at_str:
                raise ValueError("No created_at field in response")
            dt = datetime.strptime(created_at_str, '%a %b %d %H:%M:%S %z %Y')
            return dt.strftime('%Y-%m-%d_%H-%M-%S')
        except Exception as e:
            print(f"时间解析失败: {e}，使用当前时间替代")
            return datetime.now().strftime('%Y-%m-%d_%H-%M-%S')

    def get_page_type(self, response):
        """判断页面类型（多媒体、图片、视频等）"""
        if 'mix_media_info' in response:
            return 'multimedia'
        elif 'pic_infos' in response and 'pic_ids' in response and 'pic_num' in response:
            return 'images'
        elif 'page_info' in response and 'media_info' in response['page_info']:
            return 'video'
        return 'Unknown'

    def get_pic_type(self, response, pic_id):
        """获取图片类型"""
        return response['pic_infos'][pic_id]['type']

    def get_media_urls(self, response, page_type):
        """提取媒体URL"""
        media_urls = []
        if page_type == 'multimedia':
            for media in response['mix_media_info']['items']:
                if media['type'] == 'pic':
                    media_urls.append({'url': media['data']['largest']['url'],
                                      'media_id': media['data']['pic_id'],
                                      'media_type': 'pic'})
                elif media['type'] == 'video':
                    video_url = media['data']['media_info']['mp4_720p_mp4'] or media['data']['media_info']['stream_url_hd']
                    media_urls.append({'url': video_url,
                                      'media_id': media['data']['media_info']['media_id'],
                                      'media_type': 'video'})
        elif page_type == 'video':
            video_info = response['page_info']['media_info']
            video_url = video_info['playback_list'][0]['play_info']['url'] or video_info['mp4_720p_mp4'] or video_info['stream_url_hd']
            media_urls.append({'url': video_url,
                              'media_id': video_info['media_id'],
                              'media_type': 'video'})
        elif page_type == 'images':
            for pic_id in response['pic_ids']:
                pic_type = self.get_pic_type(response, pic_id)
                if pic_type == "pic":
                    media_urls.append({'url': response['pic_infos'][pic_id]['largest']['url'],
                                      'media_id': pic_id,
                                      'media_type': 'pic'})
                elif pic_type == "livephoto":
                    media_urls.append({'url': response['pic_infos'][pic_id]['video'],
                                      'media_id': pic_id,
                                      'media_type': 'livephoto'})
        return media_urls

    def clean_filename(self, name):
        """清理文件名中的非法字符"""
        return re.sub(r'[\\/*?:"<>|]', '', name).strip()

    def download_media(self, url, file_path):
        """下载单个媒体文件"""
        downloader_headers = {
            'User_Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36',
            'Referer': 'https://weibo.com/',
            'Sec_Fetch_Site': 'cross-site',
            'Cookie': self.cookie,
        }
        try:
            if os.path.isfile(file_path):
                return
            s = requests.Session()
            s.mount(url, HTTPAdapter(max_retries=5))
            for _ in range(3):
                downloaded = s.get(url, headers=downloader_headers, timeout=(5, 10))
                if (url.endswith(("jpg", "jpeg")) and not downloaded.content.endswith(b"\xff\xd9")) \
                    or (url.endswith("png") and not downloaded.content.endswith(b"\xaeB`\x82")):
                    continue
                with open(file_path, "wb") as f:
                    f.write(downloaded.content)
                    return
        except Exception as e:
            with open(os.path.join(self.base_dir, "not_downloaded.txt"), "ab") as f:
                f.write(f"{self.uid}:{file_path}:{url}\n".encode(sys.stdout.encoding))

    def download_url(self, url):
        print("Downloading URL: ", url)
        try:
            page_id = self.get_page_id(url)
            if not page_id:
                print("无法获取页面ID")
                return False

            response = self.weibo_pagesource(page_id)
            if not response:
                print("无法获取页面内容")
                return False

            # 处理内容和文件名
            publish_time = self.get_publish_time(response)
            content = response.get('text', '')
            content = re.sub(r'<[^>]+>', '', content)  # 移除HTML标签
            cleaned_content = re.sub(r'\s+', ' ', content.replace('\n', ' ')).strip()
            short_content = self.clean_filename(cleaned_content[:20].rstrip())
            folder_name = f"{publish_time}_{short_content}"
            save_path = os.path.join(self.base_dir, folder_name)
            os.makedirs(save_path, exist_ok=True)

            # 保存内容到 content.txt
            with open(os.path.join(save_path, 'content.txt'), 'w', encoding='utf-8') as f:
                f.write(f"URL: {url}\n内容: {cleaned_content}\n")

            # 下载媒体文件
            media_urls = self.get_media_urls(response, self.get_page_type(response))
            with ThreadPoolExecutor(max_workers=10) as executor:
                futures = [executor.submit(self.download_media,
                                        media['url'],
                                        os.path.join(save_path, f"{media['media_id']}.{'jpg' if media['media_type'] == 'pic' else 'mp4' if media['media_type'] == 'video' else 'mov'}"))
                        for media in media_urls]
                for future in as_completed(futures):
                    try:
                        future.result()
                    except Exception as exc:
                        print(f'下载异常: {exc}')
                        raise exc  # 抛出异常以便在外部捕获

            print(f"下载完成，文件保存在: {save_path}")
            return True  # 所有媒体下载成功

        except Exception as e:
            # 记录错误信息到 error.log
            error_log_path = os.path.join(self.base_dir, "error.log")
            timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            error_message = f"[{timestamp}] URL: {url} - Error: {str(e)}\n"
            with open(error_log_path, "a", encoding="utf-8") as f:
                f.write(error_message)
            print(f"下载失败: {url}，错误信息已记录到 {error_log_path}")
            return False


class OperationMenu:
    def __init__(self, config):
        self.config = config

    def run(self):
        uid = self.config.get_uid()
        cookie = self.config.get_cookie()
        save_dir = self.config.get_save_dir()
        os.makedirs(save_dir, exist_ok=True)

        fetcher = WeiboURLFetcher(uid=uid, cookie=cookie, base_dir=save_dir)

        # 加载 saved_urls.log 和 unsaved_urls.log 到集合
        saved_urls_path = os.path.join(fetcher.download_dir, "saved_urls.log")
        unsaved_urls_path = os.path.join(fetcher.download_dir, "unsaved_urls.log")
        saved_set = FileManager.read_urls(saved_urls_path)
        unsaved_set = FileManager.read_urls(unsaved_urls_path)

        # 优先处理 unsaved_urls.log 中的 URL
        if unsaved_set:
            print("\n正在处理未保存的 URL...")
            for url in list(unsaved_set):
                print(f"\n正在下载: {url}")
                downloader = WeiboDownloader(uid, cookie, fetcher.download_dir)  # 使用 uid 而不是 url
                if downloader.download_url(url):  # 调用 download_url 方法
                    unsaved_set.remove(url)  # 下载成功，从 unsaved_set 移除
                    saved_set.add(url)       # 添加到 saved_set
                time.sleep(1)

        print("\n正在获取用户所有微博 URL...")
        for page_urls in fetcher.get_all_urls():
            for url in page_urls:
                if url not in saved_set and url not in unsaved_set:
                    print(f"\n正在下载: {url}")
                    downloader = WeiboDownloader(uid, cookie, fetcher.download_dir)  # 使用 uid 而不是 url
                    if downloader.download_url(url):  # 调用 download_url 方法
                        saved_set.add(url)    # 下载成功，添加到 saved_set
                    else:
                        unsaved_set.add(url)  # 下载失败，添加到 unsaved_set
                    time.sleep(1)

        # 更新 unsaved_urls.log
        FileManager.update_unsaved_file(unsaved_urls_path, unsaved_set)

        print("\n所有微博内容下载完成！保存路径:", os.path.abspath(fetcher.download_dir))
