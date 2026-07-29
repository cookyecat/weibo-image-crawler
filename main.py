import argparse
from weibo import weibo_image_download as wb

parser = argparse.ArgumentParser(
                    prog='Weibo Image Crawler',
                    description='Download all the high-resolution images from weibo links.')
parser.add_argument('-l', '--link', default=None, type=str, metavar='Link',
                    help='A Weibo link to download images. For example, https://weibo.com/3178232834/MFStocIKp or https://weibo.com/3178232834/4899808463031949')
parser.add_argument('-f', '--file', default=None, type=str, metavar='File',
                    help='A file that contains lines of weibo links.')
parser.add_argument('-s', '--save', default="images", type=str, metavar='File',
                    help='Folder to save images.')

def main():
    args = parser.parse_args()

    if args.link is not None:
        wb(args.link, args.save)
    elif args.file is not None:
        links = []
        with open(args.file, 'r') as f:
            for line in f:
                line = line.strip()
                if line:
                    links.append(line)
        for url in links:
            wb(url, args.save)
    else:
        print("Please provide a weibo link.")

if __name__ == '__main__':
    main()
    
