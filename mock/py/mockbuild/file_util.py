# -*- coding: utf-8 -*-
# vim:expandtab:autoindent:tabstop=4:shiftwidth=4:filetype=python:textwidth=0:
import errno
import os
import os.path
import shutil
import stat
import subprocess
import time

from . import exception
from .trace_decorator import getLog, traceLog


@traceLog()
def mkdirIfAbsent(*args):
    for dirName in args:
        getLog().debug("ensuring that dir exists: %s", dirName)
        if not os.path.exists(dirName):
            try:
                getLog().debug("creating dir: %s", dirName)
                os.makedirs(dirName)
            except OSError as e:
                if e.errno != errno.EEXIST:
                    getLog().exception("Could not create dir %s. Error: %s", dirName, e)
                    raise exception.Error("Could not create dir %s. Error: %s" % (dirName, e))


@traceLog()
def touch(fileName):
    getLog().debug("touching file: %s", fileName)
    open(fileName, 'a').close()


@traceLog()
def rmtree(path, selinux=False, exclude=()):
    """Version of shutil.rmtree that ignores no-such-file-or-directory errors,
       tries harder if it finds immutable files and supports excluding paths"""
    if os.path.islink(path):
        raise OSError("Cannot call rmtree on a symbolic link: %s" % path)
    try_again = True
    retries = 0
    failed_to_handle = False
    failed_filename = None
    if path in exclude:
        return
    while try_again:
        try_again = False
        try:
            names = os.listdir(path)
            for name in names:
                fullname = os.path.join(path, name)
                if fullname not in exclude:
                    try:
                        mode = os.lstat(fullname).st_mode
                    except OSError:
                        mode = 0
                    if stat.S_ISDIR(mode):
                        try:
                            rmtree(fullname, selinux=selinux, exclude=exclude)
                        except OSError as e:
                            if e.errno in (errno.EPERM, errno.EACCES, errno.EBUSY):
                                # we already tried handling this on lower level and failed,
                                # there's no point in trying again now
                                failed_to_handle = True
                            raise
                    else:
                        os.remove(fullname)
            os.rmdir(path)
        except OSError as e:
            if failed_to_handle:
                raise
            if e.errno == errno.ENOENT:  # no such file or directory
                pass
            elif exclude and e.errno == errno.ENOTEMPTY:  # there's something excluded left
                pass
            elif selinux and (e.errno == errno.EPERM or e.errno == errno.EACCES):
                try_again = True
                if failed_filename == e.filename:
                    raise
                failed_filename = e.filename
                os.system("chattr -R -i %s" % path)
            elif e.errno == errno.EBUSY:
                retries += 1
                if retries > 1:
                    raise
                try_again = True
                getLog().debug("retrying failed tree remove after sleeping a bit")
                time.sleep(2)
            else:
                raise


def is_in_dir(path, directory):
    """Tests whether `path` is inside `directory`."""
    # use realpath to expand symlinks
    path = os.path.realpath(path)
    directory = os.path.realpath(directory)

    return os.path.commonprefix([path, directory]) == directory


def get_fs_type(path):
    cmd = ['/bin/stat', '-f', '-L', '-c', '%T', path]
    p = subprocess.Popen(cmd, shell=False, stdout=subprocess.PIPE,
                         universal_newlines=True)
    p.wait()
    with p.stdout as f:
        return f.readline().strip()


def find_non_nfs_dir():
    dirs = ('/dev/shm', '/run', '/tmp', '/usr/tmp', '/')
    for d in dirs:
        if not get_fs_type(d).startswith('nfs'):
            return d
    raise exception.Error('Cannot find non-NFS directory in: %s' % dirs)


def _best_effort_removal(path, use_rmtree=True):
    try:
        os.unlink(path)
    except OSError:
        pass
    if not use_rmtree:
        return
    try:
        shutil.rmtree(path)
    except OSError:
        pass


def update_tree(dest, src):
    """
    Copy files from SRC directory into DEST, recursively.  The DEST directory
    is created, including subdirectories (if not existent).  The files in DEST
    are created or updated (shutil.copy2).  If file is about to replace
    directory or vice versa, it is done without asking.  Files that are in DEST
    and not in SRC are kept untouched.
    """

    getLog().debug("Updating files in %s with files from %s", dest, src)

    mkdirIfAbsent(dest)

    for dirpath, dirnames, filenames in os.walk(src):
        raw_subpath = os.path.relpath(dirpath, src)
        subpath = os.path.normpath(raw_subpath)
        destpath = os.path.join(dest, subpath)

        for filename in filenames:
            file_from = os.path.join(dirpath, filename)
            file_to = os.path.join(destpath, filename)
            _best_effort_removal(file_to)
            shutil.copy2(file_from, file_to)

        for subdir in dirnames:
            dest_subdir = os.path.join(destpath, subdir)
            _best_effort_removal(dest_subdir, use_rmtree=False)
            mkdirIfAbsent(dest_subdir)
