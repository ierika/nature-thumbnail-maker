import sys
import os
import json
import random
from urllib.request import urlopen

from bs4 import BeautifulSoup
from PIL import Image
from PIL import ImageOps
from unipath import Path
from minio import Minio


MINIO = Minio(os.getenv('MINIO_HOST'),
              access_key=os.getenv('MINIO_ACCESS_KEY_ID'),
              secret_key=os.getenv('MINIO_SECRET_ACCESS_KEY'),
              secure=False)
BUCKET_NAME = 'natureasia-static'
WORKSPACE = Path(os.getenv('HOME')).child('Desktop')
WORKSPACE = WORKSPACE.child('THUMBNAIL_OUT')
LOCKFILE = Path('/tmp/thumbnail_maker.lock')


def exit_error(message):
    print(message)
    exit(1)


def check_requirements():
    '''Checks for requirements'''

    # Check OS requirements
    if os.name is not 'posix':
        exit_error('This script currently only supports Unix-like OS\'s.')

    # Check if theres a lock file. We only allow one process at a time.
    if LOCKFILE.exists():
        exit_error('Thumbnail maker is currently busy. Please try later.')

    # Make sure our workspace is there, if it already is, erase it,
    # and make a new one.
    try:
        if not WORKSPACE.exists():
            WORKSPACE.mkdir()
        else:
            WORKSPACE.rmtree()
            WORKSPACE.mkdir()
    except Exception as e:
        exit_error('Could not create workspace directory: {}'.format(e))

    # Check Minio
    if not Minio:
        exit_error('No access to Minio')


def get_html(articles_url):
    try:
        html = urlopen(articles_url)
    except Exception as e:
        exit_error(e)
    return html.read()


def natureasia_scraper1(html):
    bs4 = BeautifulSoup(html, 'html.parser')
    article_objects = bs4.find('', {'class': 'article-list'})
    article_objects = article_objects.findAll('article')
    doi_list = []
    for article in article_objects:
        link = article.find('', {'class': 'doi'}).get_text()
        if link:
            doi = link.split(':').pop().strip()
            doi = doi.lstrip('10.1038/')
            doi_list.append(doi)
    print('Found {} article link(s)'.format(len(doi_list)))
    return doi_list


def get_image_links(doi_list):
    domain = 'http://hub-api.live.cf.private.springer.com:80'
    api = domain + "/api/v1/articles/"
    try:
        urlopen(domain)
    except Exception as e:
        exit_error('Could not connect to Content Hub API: {}'.format(e))
    client = "?domain=nature&client=natureasia"
    image_link_list = []
    for doi in doi_list:
        try:
            resp = urlopen(api + doi + client)
        except Exception as e:
            print(e)
            continue
        jsonObj = json.load(resp)
        image_link = jsonObj['article']['hasImage']
        image_link = image_link['hasImageAsset']['link'] or None
        if image_link:
            print('Found image asset of {}'.format(doi))
            image_link_list.append((doi, image_link))
    return image_link_list


def download_image(doi, link):
    file_extension = link.split('/').pop()
    file_extension = file_extension.split('.').pop()
    file_name = '{}.{}'.format(doi, file_extension)
    file_name = WORKSPACE.child(file_name)
    print('Downloading ' + link)
    try:
        binary = urlopen(link)
        with open(file_name, 'wb') as f:
            f.write(binary.read())
    except Exception as e:
        print('ERROR: could not download {} image: {}'.format(doi, e))
    return file_name


def make_thumbnail(img, min_size=200, fill_color=(255, 255, 255), mode='pad'):
    if mode == 'pad':
        x, y = img.size
        size = max(min_size, x, y)
        thumbnail = Image.new('RGB', (size, size), fill_color)
        thumbnail.paste(img, ((size - x) // 2, (size - y) // 2))
        thumbnail = thumbnail.resize((min_size, min_size), Image.ANTIALIAS)
    elif mode == 'crop':
        thumbnail = ImageOps.fit(img, (min_size, min_size), Image.ANTIALIAS)
    else:
        exit_error('Thumbnail mode: {} is invalid.'.format(mode))
    return thumbnail


def convert_to_jpeg(img):
    if img.format is not 'JPEG':
        return img.convert(mode='RGB')
    return img


def parse_args():
    help_msg = '''
        USAGE: python thumbnail_maker.py <journal_shortname> <mode[crop|pad]>
        '''
    if len(sys.argv) == 3:
        return (sys.argv[1], sys.argv[2])
    elif len(sys.argv) > 3:
        print(help_msg)
        exit_error('Entered too many arguments.')
    elif len(sys.argv) < 3:
        print(help_msg)
        exit_error('Entered too few arguments.')
    else:
        exit_error(help_msg)


def upload_file(journal_shortname=None, file=None):
    '''Uploads the file to Minio server'''
    file_name = file.name
    key = 'ja-jp/{journal_shortname}/img/articles/{file_name}'.format(
        journal_shortname=journal_shortname,
        file_name=file_name,
    )
    try:
        MINIO.fput_object(BUCKET_NAME, key, file)
        print('Uploaded {}'.format(file))
    except Exception as e:
        exit_error(e)


def lock():
    LOCKFILE.write_file('')


def unlock():
    if LOCKFILE.exists():
        LOCKFILE.rmtree()


if __name__ == '__main__':
    try:
        # Check for requirements above anything else
        check_requirements()
        lock()
        journal_shortname, mode = parse_args()
        # Load
        articles_url = 'https://www.natureasia.com/ja-jp/{}/articles?v={}'.format(
            journal_shortname,
            random.randint(1, 1000))
        print('Loading ' + articles_url)
        html = get_html(articles_url)
        print('Successfully loaded.')

        # Scrape.
        '''
        We could have have one or more scrapers in the future.
        So I made a tuple of would be scrapers.
        We are scraping because these articles that we're looking for are
        hard-coded.
        '''
        doi_list = []
        natureasia_scrapers = (natureasia_scraper1,)
        for scraper in natureasia_scrapers:
            try:
                doi_list = scraper(html)
            except Exception as e:
                print('Trying to scrape with another scraper...')
                continue

        if not doi_list:
            exit_error('Could not scrape HTML')

        # Scrape each article links for image
        image_link_list = get_image_links(doi_list)

        # Download the images
        file_list = []
        for doi, link in image_link_list:
            file_list.append(download_image(doi, link))

        # Convert the images to a thumbnail
        for file in file_list:
            img = Image.open(file)
            img = convert_to_jpeg(img)
            thumbnail = make_thumbnail(img, mode=mode)
            thumbnail.save(img.filename, quality=70, format='jpeg')
            print('Thumbnail for {} is generated.'.format(img.filename))

        # Upload thumbnails to Minio. First let's check if the bucket exists.
        if MINIO.bucket_exists(BUCKET_NAME):
            for file in file_list:
                upload_file(journal_shortname=journal_shortname, file=file)
        else:
            exit_error('{} doesn\'t exist.'.format(BUCKET_NAME))

        # Finish
        print('DONE')
        unlock()
    except KeyboardInterrupt as e:
        print('Aborting...')
    finally:
        unlock()
        exit()
