# weibo-image-crawler

从一条特定的微博中下载所有的高清大图、livephoto 动图、高清视频。

无需登录账号。

# Updates

**7/29/2026**: 微博更新了接口，导致原下载方法失效。本次更新了下载方法，可继续免登录下载最高清晰度的媒体。

**4/4/2024**: 因微博接口的改变，爬取收费图片的方式失效。正在尝试其他方法...

**3/23/2024**: 增加了 2K 60 帧高清视频下载 (如果没有 2K 视频，则自动选择最高质量视频下载)

**3/23/2024**: 增加了对微博新接口的支持

**3/23/2024**: 增加了对 t.cn 短链的支持

**1/7/2024**: 增加了图片和视频的并行下载

## Usage:

1. 获取一条微博链接。链接的形式有两种：
- t.cn短链:  http://t.cn/A6YBzjqQ
- 短码链接： https://weibo.com/2343372547/O6kbQmBwX
- 长码链接： https://weibo.com/2117508734/5014997330298194

这两个链接指向同一个微博页面。

2.
下载一条微博的所有高清大图/livephoto动图/高清视频:
```
python main.py -l https://weibo.com/2343372547/O6kbQmBwX
或者
python main.py -l http://t.cn/A6YBzjqQ
或者
python main.py -l https://weibo.com/2117508734/5014997330298194
```

下载多条微博的所有高清大图/livephoto动图/高清视频，可以把微博链接放到一个文本文件里，每个链接占一行:
```
python main.py -f links.txt
```

设定图片存储到本地 images 目录下:
```
python main.py -f links.txt -s images
```