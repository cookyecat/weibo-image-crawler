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
        return re.sub(r'[\\/*?:"<>|]', '', name).strip()

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

class WeiboPostDownloader:
    def __init__(self, url, save_dir, cookie):
        self.url = url
        self.save_dir = save_dir
        self.cookie = cookie
        self.session = requests.Session()
        retry_strategy = Retry(total=3, status_forcelist=[429, 500, 502, 503, 504], allowed_methods=["HEAD", "GET", "OPTIONS"])
        adapter = HTTPAdapter(max_retries=retry_strategy)
        self.session.mount("https://", adapter)
        self.session.mount("http://", adapter)
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36',
            'Cookie': self.cookie,
            'Referer': 'https://weibo.com/'
        })

    def fetch_page_source(self, page_id):
        """获取微博页面数据"""
        response = self.session.get(f"https://weibo.com/ajax/statuses/show?id={page_id}")
        return response.json() if response.ok else None

    def determine_type(self, response):
        """判断微博页面类型"""
        if 'mix_media_info' in response:
            return 'multimedia'
        elif 'pic_infos' in response and 'page_info' not in response:
            return 'images'
        elif 'page_info' in response:
            return 'video'
        return 'unknown'

    def extract_media_urls(self, response):
        """根据页面类型提取媒体 URL"""
        page_type = self.determine_type(response)
        media_urls = []
        if page_type == 'multimedia':
            for item in response['mix_media_info']['items']:
                if item['type'] == 'pic':
                    media_urls.append({'url': item['data']['largest']['url'], 'media_id': item['data']['pic_id'], 'media_type': 'pic'})
                elif item['type'] == 'video':
                    video_url = item['data']['media_info'].get('mp4_720p_mp4') or item['data']['media_info'].get('stream_url_hd')
                    media_urls.append({'url': video_url, 'media_id': item['data']['media_info']['media_id'], 'media_type': 'video'})
        elif page_type == 'video':
            video_info = response['page_info']['media_info']
            video_url = video_info['playback_list'][0]['play_info']['url'] or video_info.get('mp4_720p_mp4') or video_info.get('stream_url_hd')
            media_urls.append({'url': video_url, 'media_id': video_info['media_id'], 'media_type': 'video'})
        elif page_type == 'images':
            for pic_id in response['pic_ids']:
                pic_type = response['pic_infos'][pic_id]['type']
                if pic_type == "pic":
                    media_urls.append({'url': response['pic_infos'][pic_id]['largest']['url'], 'media_id': pic_id, 'media_type': 'pic'})
                elif pic_type == "livephoto":
                    media_urls.append({'url': response['pic_infos'][pic_id]['video'], 'media_id': pic_id, 'media_type': 'livephoto'})
        return media_urls

    def download_media(self, url, file_path):
        """下载媒体文件"""
        if os.path.exists(file_path):
            return
        try:
            with self.session.get(url, timeout=10) as resp:
                with open(file_path, 'wb') as f:
                    f.write(resp.content)
        except Exception as e:
            print(f"下载失败: {url} - {str(e)}")
            with open("download_errors.log", "a") as f:
                f.write(f"{url}\t{file_path}\t{str(e)}\n")
            raise

    def download(self):
        """下载单个微博的内容和媒体"""
        print(f"\n正在处理微博: {self.url}")
        try:
            page_id = WeiboUtils.get_page_id(self.url)
            if not page_id:
                raise Exception("无效的微博 URL")
            response = self.fetch_page_source(page_id)
            if not response:
                raise Exception("获取微博数据失败")

            # 清理内容并生成文件名
            raw_content = response.get('text', '无内容')
            cleaned_content = WeiboUtils.clean_content(raw_content)
            publish_time = WeiboUtils.get_publish_time(response)
            folder_name = f"{publish_time}_{cleaned_content}"
            save_path = os.path.join(self.save_dir, folder_name)
            os.makedirs(save_path, exist_ok=True)

            # 保存清理后的内容
            with open(os.path.join(save_path, 'content.txt'), 'w', encoding='utf-8') as f:
                f.write(f"URL: {self.url}\n原始内容: {raw_content}\n清理后内容: {cleaned_content}\n")

            # 下载媒体文件
            media_urls = self.extract_media_urls(response)
            print(f"发现 {len(media_urls)} 个媒体文件")
            i = 1
            with ThreadPoolExecutor(max_workers=5) as executor:
                futures = []
                for media in media_urls:
                    ext = 'jpg' if media['media_type'] == 'pic' else 'mov' if media['media_type'] == 'livephoto' else 'mp4'
                    fpath = os.path.join(save_path, f"{i}.{ext}")
                    futures.append(executor.submit(self.download_media, media['url'], fpath))
                    i += 1
                for future in as_completed(futures):
                    future.result()

            # 记录日志
            date_log_path = os.path.join(self.save_dir, "date.log")
            saved_urls_log_path = os.path.join(self.save_dir, "saved_urls.log")
            FileManager.append_date(date_log_path, publish_time.replace('-', ''))
            FileManager.append_url(saved_urls_log_path, self.url)
        except Exception as e:
            print(f"处理微博时发生错误: {str(e)}")
            unsaved_urls_log_path = os.path.join(self.save_dir, "unsaved_urls.log")
            FileManager.append_url(unsaved_urls_log_path, self.url)

class OperationMenu:
    def __init__(self, config):
        self.config = config

    def run(self):
        uid = self.config.get_uid()
        cookie = self.config.get_cookie()
        save_dir = self.config.get_save_dir()
        os.makedirs(save_dir, exist_ok=True)

        print("\n正在获取用户所有微博 URL...")
        fetcher = WeiboURLFetcher(uid=uid, cookie=cookie, base_dir=save_dir)
        for page_urls in fetcher.get_all_urls():
            for url in page_urls:
                print(f"\n正在下载: {url}")
                downloader = WeiboPostDownloader(url, fetcher.download_dir, cookie)
                downloader.download()
                time.sleep(1)

        print("\n所有微博内容下载完成！保存路径:", os.path.abspath(fetcher.download_dir))

