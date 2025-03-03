#支持批量下载uid
import argparse
import os
from weibo import Config
from weibo import OperationMenu

parser = argparse.ArgumentParser(
    prog='Weibo Image Crawler',
    description='Download all the high-resolution images from weibo links.'
)
parser.add_argument('-u', '--uid', default=None, type=str, metavar='Link',
                    help='Specify a single Weibo UID to download')
parser.add_argument('-c', '--cookie', default=None, type=str,
                    metavar='Cookie', help='Authentication cookie')
parser.add_argument('-s', '--save', default="images", type=str, metavar='File',
                    help='Folder to save images.')
#从文件读取uid
parser.add_argument('-f', '--file', default=None, type=str, metavar='File',
                    help='Path to the file containing UIDs (one UID per line)')
#python main1.py -f C:\Computer\Code666\python\live\uids.txt -c "XSRF-TOKEN=K9F9W791GF-bv4WMl5Ma9laq; SCF=AoXKxNDR2KAE7RBWtqTe_k17pdQ99qmLYtIpZ3yTsWopvU7lJZuCpsmB_6B32OyA_O5ljSGzBV6ktbi1EG7ZAwE.; _s_tentry=passport.weibo.com; Apache=9523461103546.223.1740211781881; SINAGLOBAL=9523461103546.223.1740211781881; ULV=1740211781975:1:1:1:9523461103546.223.1740211781881:; ALF=1743408601; SUB=_2A25KxrCJDeRhGeBO61QU9C3KzDiIHXVpukxBrDV8PUJbkNB-LXL8kW1NSl3SrVSP8JzxvdTPLsy6NGVFJKXJuZze; SUBP=0033WrSXqPxfM725Ws9jqgMF55529P9D9WF.VeSDo5vVWMQiHi-nPJpF5JpX5KMhUgL.Foq7ehqfShecS0B2dJLoIceLxK-L1-qLB.2LxK.L1hML12eLxKqL1-zLB.eLxK-L1KzL12eLxKBLB.zL1KqLxKML1-2L1hBLxKML1h2LBo-LxKqL1KML12qLxK-LBK-LBoeLxKqL1-eL12zLxK-L12qL12zt; WBPSESS=ehsSjE2RCvHbcziN34FGv45cpmz8bpMYzAJRio_WbIUbXZyG_BZ-rFAgNyYeyComBnrOaKvnzInI1We4qpjH_7NgXKGdt8EqMzU8W5Yq2f34hgam1j0j4fAVyBBWi3_7REPQoQ-XaGh7-QbeQwoiDA==" -s "C:\Base1\新建文件夹"

def main():
    args = parser.parse_args()
    uid_default = ["1923024604", "5491928243"]  # 默认UID列表
    uids = []

    # 如果命令行指定了UID，则优先使用
    if args.uid is not None:
        uids = [args.uid]
    else:
        # 如果指定了文件路径，则从文件中读取UID列表
        if args.file is not None:
            try:
                with open(args.file, 'r', encoding='utf-8') as f:
                    uids = [line.strip() for line in f if line.strip()]
            except FileNotFoundError:
                print(f"错误：文件 '{args.file}' 未找到，使用默认UID列表。")
        else:
            # 如果未指定文件路径，尝试从默认文件读取UID列表
            default_file = os.path.join(os.path.dirname(__file__), 'uids.txt')
            try:
                with open(default_file, 'r', encoding='utf-8') as f:
                    uids = [line.strip() for line in f if line.strip()]
            except FileNotFoundError:
                pass
        
        # 如果文件不存在或内容为空，使用默认值
        if not uids:
            uids = uid_default
    
    # 遍历所有UID并执行下载任务
    for uid in uids:
        config = Config(
            uid=uid,
            cookie=args.cookie,
            save_dir=args.save
        )
        OperationMenu(config).run()

if __name__ == "__main__":
    main()
