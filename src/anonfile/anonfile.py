#!/usr/bin/env python3

"""
The MIT License

Copyright (c) 2021, Nicholas Bruce Strydom

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in
all copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN
THE SOFTWARE.
"""

from __future__ import annotations

import html
import os
import re
import sys
from dataclasses import dataclass
from functools import wraps
from pathlib import Path
from typing import List, Tuple
from urllib.parse import ParseResult, urljoin, urlparse
from urllib.request import getproxies

import requests
from faker import Faker
from requests import Session
from requests.adapters import HTTPAdapter
from requests.models import Response
from tqdm import tqdm
from tqdm.utils import CallbackIOWrapper
from urllib3 import Retry

__version__ = "0.2.4"
package_name = "anonfile"
python_major = "3"
python_minor = "7"

try:
    assert sys.version_info >= (int(python_major), int(python_minor))
except AssertionError:
    raise RuntimeError(f"{package_name!r} requires Python {python_major}.{python_minor}+ (You have Python {sys.version})")


@dataclass(frozen=True)
class ParseResponse:
    response: Response
    file_path: Path

    @property
    def json(self) -> dict:
        """
        Return the entire HTTP response.
        """
        return self.response.json()

    @property
    def status(self) -> bool:
        """
        Return the upload status. If `false`, an error message indicating the
        cause for the malfunction will be redirected to `sys.stderr`.
        """
        status = bool(self.json['status'])

        if not status:
            print(self.json['error']['message'], file=sys.stderr)
            print(self.json['error']['type'], file=sys.stderr)
            print(self.json['error']['code'], file=sys.stderr)

        return status

    @property
    def url(self) -> ParseResult:
        """
        Return the URL associated with the uploaded file.
        ```
        """
        return urlparse(self.json['data']['file']['url']['full'])

    #region metadata

    @property
    def id(self) -> str:
        """
        Return the ID (path) of the uploaded file. 
        """
        return self.json['data']['file']['metadata']['id']

    @property
    def name(self) -> Path:
        """
        Return the filename of the uploaded file.
        """
        return Path(self.json['data']['file']['metadata']['name'])

    @property
    def size(self) -> int:
        """
        Return the uploaded file size in bytes.
        """
        return int(self.json['data']['file']['metadata']['size']['bytes'])

    #endregion

class AnonFile:
    _timeout = (5, 5)
    _total = 5
    _status_forcelist = [413, 429, 500, 502, 503, 504]
    _backoff_factor = 1
    _fake = Faker()

    API = "https://api.anonfiles.com/11ec585d4823616f"

    __slots__ = ['endpoint', 'token', 'timeout', 'total', 'status_forcelist', 'backoff_factor']

    def __init__(self, 
                 token: str="",
                 timeout: Tuple[float,float]=_timeout,
                 total: int=_total,
                 status_forcelist: List[int]=_status_forcelist,
                 backoff_factor: int=_backoff_factor) -> AnonFile:
        self.token = token
        self.timeout = timeout
        self.total = total,
        self.status_forcelist = status_forcelist,
        self.backoff_factor = backoff_factor

    @staticmethod
    def __progressbar_options(iterable, desc, unit, color: str="\033[32m", char='\u25CB', total=None, disable=False) -> dict:
        """
        Return custom optional arguments for `tqdm` progressbars.
        """
        return {
            'iterable': iterable,
            'bar_format': "{l_bar}%s{bar}%s{r_bar}" % (color, "\033[0m"),
            'ascii': char.rjust(9, ' '), 
            'desc': desc, 
            'unit': unit.rjust(1, ' '), 
            'unit_scale': True,
            'unit_divisor': 1024,
            'total': len(iterable) if total is None else total, 
            'disable': not disable
        }

    @property
    def retry_strategy(self) -> Retry:
        """
        The retry strategy returns the retry configuration made up of the
        number of total retries, the status forcelist as well as the backoff
        factor. It is used in the session property where these values are 
        passed to the HTTPAdapter. 
        """
        return Retry(total=self.total,
            status_forcelist=self.status_forcelist,
            backoff_factor=self.backoff_factor
        )

    @property
    def session(self) -> Session:
        """
        Create a custom session object. A request session provides cookie
        persistence, connection-pooling, and further configuration options.
        """
        assert_status_hook = lambda response, *args, **kwargs: response.raise_for_status()
        session = requests.Session()
        session.mount("https://", HTTPAdapter(max_retries=self.retry_strategy))
        session.hooks['response'] = [assert_status_hook]
        session.headers.update({
            "User-Agent" : AnonFile._fake.chrome(version_from=80, version_to=86, build_from=4100, build_to=4200)
        })
        return session

    def authenticated(func):
        @wraps(func)
        def wrapper(self, *args, **kwargs):
            try:
                if self.token is not None:
                    return func(self, *args, **kwargs)
                else:
                    raise Exception("[!] Error: API key is not configured.")
            except Exception as exception:
                print(exception, file=sys.stderr)
        return wrapper

    @authenticated
    def upload(self, path: str, progressbar: bool=False) -> ParseResponse:
        """
        Upload a file located in `path` to http://anonfiles.com.

        Example
        -------

        ```
        from anonfile import AnonFile

        anon = AnonFile('my_token')
        result = anon.upload('test.txt')

        # https://anonfiles.com/9ee1jcu6u9/test_txt
        print(result.url.geturl())
        ```

        Note
        ----
        - `AnonFile` offers unlimited bandwidth
        - Uploads cannot exceed a file size of 20G
        """
        size = os.stat(path).st_size
        options = AnonFile.__progressbar_options(None, f"Upload: {Path(path).name}", unit='B', total=size, disable=progressbar)
        with open(path, mode='rb') as file_handler:
            with tqdm(**options) as tqdm_handler:
                response = self.session.post(
                    urljoin(AnonFile.API, 'upload'),
                    params={'token': self.token},
                    files={'file': CallbackIOWrapper(tqdm_handler.update, file_handler, 'read')},
                    timeout=self.timeout,
                    proxies=getproxies(),
                    verify=True
                )
        
                return ParseResponse(response, Path(path))

    @authenticated
    def download(self, url: str, path: Path=Path.cwd(), progressbar: bool=False) -> ParseResponse:
        """
        Download a file from https://anonfiles.com given a `url`. Set the download
        directory in `path` (uses the current working directory by default).

        Example
        -------

        ```
        from pathlib import Path
        from anonfile import AnonFile

        anon = AnonFile('my_token')
        target_dir = Path.home().joinpath('Downloads')
        result = anon.download("https://anonfiles.com/9ee1jcu6u9/test_txt", target_dir)
        # WindowsPath('C:/Users/username/Downloads/test.txt')
        print(result.file_path)
        ```
        """
        get = lambda url, **kwargs: self.session.get(url, timeout=self.timeout, proxies=getproxies(), **kwargs)

        info = get(urljoin(AnonFile.API, f"v2/file/{urlparse(url).path.split('/')[1]}/info"))
        info.encoding = 'utf-8'

        links = re.findall(r'''.*?href=['"](.*?)['"].*?''', html.unescape(get(url).text), re.I)
        download_link = next(filter(lambda link: 'cdn-' in link, links))
        file_path = path.joinpath(Path(urlparse(download_link).path).name)
        download = ParseResponse(info, file_path)

        options = AnonFile.__progressbar_options(None, f"Download {download.id}", unit='B', total=download.size, disable=progressbar)
        with open(file_path, mode='wb') as file_handler:
            with tqdm(**options) as tqdm_handler:
                for chunk in get(download_link, stream=True).iter_content(1024):                
                    tqdm_handler.update(len(chunk))
                    file_handler.write(chunk)

        return download
