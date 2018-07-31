#!/usr/bin/env Python3
import re
import sys
import os
import json
import random
from urllib.request import urlopen
from urllib.parse import urlencode

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
    """
    Exits shell with the status code of 1

    :param message: error message to be shown
    :type message: string
    """
    print(message)
    exit(1)


def check_requirements():
    """
    Checks for requirements
    """
    # Check OS requirements
    if os.name is not 'posix':
        exit_error('This script currently only supports Unix-like OS\'s.')

    # Check if there's a lock file. We only allow one process at a time.
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
    """
    Downloads articles page HTML.

    So it can be parsed.

    :param articles_url: URL of the page to scrape
    :type articles_url: string
    :return: The HTML content
    :rtype: string
    """
    html = None
    try:
        html = urlopen(articles_url)
    except Exception as e:
        exit_error(e)
    return html.read()


def natureasia_scraper1(html):
    """
    Scrapes articles page.

    This is just one of the scrapers.
    There may be more once there's a need for it.

    :param html: A String containing an HTML markup
    :type: string
    :return: A list pf strings containing DOIs
    :rtype: list
    """
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
    """
    Get image link list

    :param doi_list: list of DOI without the '10.1038'
    :type doi_list: list
    :return: a list of tuples containing the DOI and the image asset URL
    :rtype: list
    """
    domain = 'http://hub-api.live.cf.private.springer.com:80'
    api = domain + "/api/v1/articles"
    try:
        urlopen(domain)
    except Exception as e:
        exit_error('Could not connect to Content Hub API: {}'.format(e))
    query_params = {
        'domain': 'nature',
        'client': 'natureasia',
    }
    image_link_list = []
    for doi in doi_list:
        try:
            resp = urlopen('{api_endpoint}/{doi}/?{query_params}'.format(
                api_endpoint=api,
                doi=doi,
                query_params=urlencode(query_params),
                ))
            jsonObj = json.load(resp)
            image_link = jsonObj['article']['hasImage']
            image_link = image_link['hasImageAsset']['link']
            if image_link:
                print('Found image asset of {}'.format(doi))
                image_link_list.append((doi, image_link))
        except Exception as e:
            print(e)
            continue
    return image_link_list


def download_image(doi, link):
    """
    Downloads image link

    :param doi
    :param link: URL to the image asset
    :type doi: string
    :type link: string
    :return: a file path to the downloaded image
    :rtype: a Path object
    """
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
    """
    Makes a thumbnail.

    :param img: an Image object
    :param min_size: The minimum width for the thumbnail
    :param fill_color: RGB color
    :param mode: Resizing mode. pad or crop.
    :type img: Image
    :type min_size: int
    :type fill_color: tuple
    :type mode: string
    :return: an Image object
    :type: Image
    """
    thumbnail = None
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
    """
    Converts all image to JPEG.

    :param img
    :type img: An Image object
    :return: Returns the same image object. Just converted to RGB.
    :rtype: Image
    """
    if img.format is not 'JPEG':
        return img.convert(mode='RGB')
    return img


def parse_args():
    """
    Parses command-line arguments
    """
    help_msg = """
        USAGE: python thumbnail_maker.py <journal_shortname> <mode[crop|pad]>
        """
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
    """
    Uploads the file to Minio server

    :param journal_shortname: The journal short name
    :param file: a file path to the image file to be uploaded
    :type journal_shortname: string
    :type file: Path
    """
    file = Path(file)
    file_name = file.name
    key = 'ja-jp/{journal_shortname}/img/articles/{file_name}'.format(
        journal_shortname=journal_shortname,
        file_name=file_name,
    )
    try:
        MINIO.fput_object(BUCKET_NAME, key, file)
        print('Uploaded {}'.format(file))
    except Exception as e:
        exit_error(str(e))


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
        params = urlencode({'v': random.randint(1, 1000)})
        # Load
        articles_url = """
            https://www.natureasia.com/ja-jp/{}/articles?{}
            """.format(
            journal_shortname,
            params).strip()
        print('Loading ' + articles_url)
        html = get_html(articles_url)
        print('Successfully loaded.')

        # Scrape.
        """
        We could have have one or more scrapers in the future.
        So I made a tuple of would be scrapers.
        We are scraping because these articles that we're looking for are
        hard-coded.
        """
        doi_list = []
        natureasia_scrapers = (natureasia_scraper1,)
        for scraper in natureasia_scrapers:
            try:
                doi_list = scraper(html)
                if doi_list:
                    break
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
            file_path = download_image(doi, link)
            file_list.append(file_path)

        # Convert the images to a thumbnail
        upload_list = []
        for file in file_list:
            img = Image.open(file)
            img = convert_to_jpeg(img)

            try:
                thumbnail = make_thumbnail(img, mode=mode)
                filename = re.sub(r'\.(png|tiff|bmp)$', '.jpg', file)
                thumbnail.save(filename, quality=70, format='jpeg')
                print('Thumbnail for {} is generated.'.format(filename))
                upload_list.append(filename)
            except Exception as e:
                print(str(e))


        # Upload thumbnails to Minio. First let's check if the bucket exists.
        if MINIO.bucket_exists(BUCKET_NAME):
            for file in upload_list:
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
