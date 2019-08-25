#!/usr/bin/env python3
# coding=UTF8
"""Upgrades all comics with manga-py.
From every configured paths it suposes that every directory is a comic folder
and downloads the latest volumes with manga-py.
It infers the url from a config file or from the last modified *.cbr in the
folder.
"""

# https://github.com/yuru-yuri/manga-dl/issues/150

import datetime
import json
import pathlib
import re
import sys
import time
import zipfile

import click
from loguru import logger
import sh

PREFERRED_ARGS = [
    "--cbz", "--zero-fill", "--rename-pages", "--no-webp",
    "--show-current-chapter-info"
]  # , "--one-thread"]
# ~ PREFERRED_KWARGS = {}
PREFERRED_LANG = 'gb'
SKIPPED = []  # ['kissmanga', 'mangadex']
SLEEP_SECONDS_BETWEEN_DOWNLOADS = 0
MINUTES_TO_WARNING_BETWEEN_DOWNLOADS = 2
SINGLE_COMIC_CONFIG_FILE_NAME = 'info.json'


def get_last_file(paths):
    """Returns last modified file from list of paths"""
    if paths:
        return sorted(paths, key=lambda f: f.stat().st_mtime)[-1]
    return ""


class Comic:
    """Comics contained in a folder"""

    def __init__(self, library, path):
        self.library = library
        self.path = pathlib.Path(path)
        self.config_file = self.path / SINGLE_COMIC_CONFIG_FILE_NAME
        self.initial_volumes = self._get_actual_comic_volumes()
        self.last_file = get_last_file(self.initial_volumes)
        self.url = None
        self.url_from_cbz = False
        self._get_url()
        self.downloaded_volumes = set()

    def _get_url(self):
        self._get_url_from_config()
        if not self.url and self.initial_volumes:
            self._get_url_from_cbz()
            if self.url:
                comic_config = {'url': self.url}
                with self.config_file.open("w") as f_config_file:
                    json.dump(
                        comic_config, f_config_file, sort_keys=True, indent=4)

    def _get_url_from_config(self):
        if self.config_file.exists():
            try:
                with self.config_file.open() as f_config_file:
                    comic_config = json.load(f_config_file)
                    self.url = comic_config['url']
                    # TODO: Use more args in info.json
                    # ~ comic_args = copy.deepcopy(PREFERRED_ARGS)
                    # ~ comic_kwargs = copy.deepcopy(PREFERRED_KWARGS)
            except (KeyError, json.decoder.JSONDecodeError) as e:
                click.echo(
                    click.style(
                        "Excepcion reading config file: {e}".format(e=e),
                        fg='yellow'))

    def _get_url_from_cbz(self):
        try:
            with zipfile.ZipFile(str(self.last_file)) as zip_file:
                with zip_file.open('info.txt') as zip_file_info:
                    zip_file_info_content = zip_file_info.read()
                    site_matchs = re.search(r"Site: (.*)",
                                            zip_file_info_content.decode(),
                                            re.MULTILINE)
                    if site_matchs:
                        self.url = site_matchs.groups()[0]
                        self.url_from_cbz = True
        except KeyError as e:
            click.echo(
                click.style(
                    "Excepcion reading zip file: {e}".format(e=e), fg='red'))

    def _get_actual_comic_volumes(self):
        # ~ import pudb; pu.db
        if self.path.is_dir():
            volumes = set(self.path.glob('*.cbz'))
        elif self.path.is_symlink():
            click.echo(click.style("Symlink: {}".format(self.path), fg='red'))
            volumes = set()
        else:
            self.path.mkdir()
            # TODO: Flag new_path?
            volumes = set()
        return volumes

    def _download_comic(self):
        if not self.url:
            click.echo(click.style("No url available", fg='red'))
        extra_args = {}
        if 'mangadex' in self.url:
            extra_args['_in'] = PREFERRED_LANG  # Type lang code in sh stdin
        try:
            sh.manga_py(
                "--destination",
                self.path.parent,
                "--name",
                self.path.name,
                self.url,
                *PREFERRED_ARGS,
                _out=sys.stdout,
                _err=sys.stderr,
                **extra_args)
        except Exception as e:
            click.echo(
                click.style(
                    "Excepcion downloading comic: {e}".format(e=e), fg='red'))

    def update(self):
        """Check for new comics and download if possible"""

        def _msg_folder(path):
            click.echo(
                click.style("\t📂 ", fg='blue') + click.style(
                    "file://{d}".format(d=path), fg='cyan'))

        def _msg_url(url, last_file, url_from_cbz):
            msg = (click.style(
                "\t🗎 {last_file}\n\t🔗  {url}".format(
                    last_file=last_file, url=url),
                fg='blue'))
            if url_from_cbz:
                msg += click.style(" 🗜 ", fg='green')
            click.echo(msg)

        def _msg_time_spent(time_end, time_init):
            time_delta = time_end - time_init
            if time_delta > datetime.timedelta(
                    minutes=MINUTES_TO_WARNING_BETWEEN_DOWNLOADS):
                time_color = 'yellow'
            else:
                time_color = 'cyan'
            click.echo(
                click.style("\t⏰  ", fg='blue') + click.style(
                    "{time_spent}".format(time_spent=str(time_delta)),
                    fg=time_color))

        def _msg_empty_folder():
            click.echo(click.style("No *.cbz here!!!", fg='yellow'))

        time_init = datetime.datetime.now()

        _msg_folder(self.path)
        if not self.initial_volumes:
            _msg_empty_folder()
            return
        _msg_url(self.url, self.last_file, self.url_from_cbz)

        self._download_comic()
        self.downloaded_volumes = (
            self._get_actual_comic_volumes() - self.initial_volumes)
        time.sleep(SLEEP_SECONDS_BETWEEN_DOWNLOADS)
        time_end = datetime.datetime.now()

        _msg_time_spent(time_end, time_init)


class Library:
    """Folder containing one comic per subfolder"""

    def __init__(self, base_dirs):
        self.base_dirs = [
            pathlib.Path(path).expanduser().resolve() for path in base_dirs
        ]
        self.comics = []
        self.downloaded_volumes = set()

        for base_dir in self.base_dirs:
            for path in sorted(
                    list(base_dir.glob("*")),
                    key=lambda x: x.stat().st_mtime,
                    reverse=True):
                self.comics.append(Comic(library=self, path=path))

    def update(self):
        """Check all comics for updates"""

        def _msg_downloaded_comics(present, max_num):
            msg = (click.style("(", fg='blue') + click.style(
                "{present}/{max_num}".format(present=present, max_num=max_num),
                fg='cyan') + click.style(")", fg='blue'))
            click.echo(msg)

        def _msg_downloaded_by_comic(now, total):
            num_tot = len(total)
            num_now = len(now)
            num_previous = num_tot - num_now
            click.echo(
                click.style(
                    "\tTotal downloaded: ({} ".format(num_previous), fg='blue')
                + click.style("+ {} = {}".format(num_now, num_tot),
                              fg='cyan') + click.style(")\n", fg='blue'))

        def _msg_downloaded_total(total):
            click.echo(
                click.style("\nFiles downloaded:\n", fg='blue') + click.style(
                    "{files}".format(files="\n".join(
                        [str(x) for x in sorted(total)])),
                    fg='green'))

        def _needs_skip(url):
            skip = any(word in url for word in SKIPPED)
            if skip:
                click.echo(
                    click.style("Skipped {url}".format(url=url), fg='yellow'))
            return skip

        max_num = len(self.comics)
        for num, comic in enumerate(self.comics, start=1):
            _msg_downloaded_comics(num, max_num)
            if _needs_skip(comic.url):
                continue
            comic.update()
            self.downloaded_volumes |= comic.downloaded_volumes
            _msg_downloaded_by_comic(comic.downloaded_volumes,
                                     self.downloaded_volumes)

        _msg_downloaded_total(self.downloaded_volumes)


@click.command()
@click.argument('path', default='.', type=click.Path(exists=True))
@logger.catch
def update_comics(path):
    """Check all comics for updates"""
    library_paths = []  # TODO: yaml config file
    library_paths.append(path)
    library = Library(library_paths)
    library.update()


if __name__ == '__main__':
    update_comics()
