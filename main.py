import argparse
from weibo import Config
from weibo import OperationMenu

parser = argparse.ArgumentParser(
    prog='Weibo Image Crawler',
    description='Download all the high-resolution images from weibo links.')
parser.add_argument('-u', '--uid', default=None, type=str, metavar='Link',
                    help='1923024604')
parser.add_argument('-c', '--cookie', default=None, type=str,  # 新增cookie参数
                    metavar='Cookie', help='Authentication cookie')
parser.add_argument('-s', '--save', default="images", type=str, metavar='File',
                    help='Folder to save images.')

def main():
    args = parser.parse_args()  # 解析命令行参数
    config = Config(
        uid=args.uid, 
        cookie=args.cookie,  # 添加cookie参数
        save_dir=args.save
    )
    menu = OperationMenu(config)
    menu.run()

if __name__ == "__main__":
    main()
