"""Descriptor-based owner-only credential reads on qualified POSIX platforms."""
import errno
import os
import stat
import sys


def read_private(path, limit):
    descriptor=os.open(path,os.O_RDONLY|os.O_NONBLOCK|os.O_NOFOLLOW)
    with os.fdopen(descriptor,'rb') as stream:
        info=os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_uid!=os.getuid() or info.st_mode & 0o077:
            raise ValueError('credential file must be owner-only regular input')
        if sys.platform=='linux':
            try:
                os.getxattr(stream.fileno(),'system.posix_acl_access')
            except OSError as exc:
                if exc.errno!=errno.ENODATA:raise ValueError('credential ACL inspection refused') from None
            else:raise ValueError('credential ACL refused')
        elif sys.platform=='darwin':
            import ctypes
            libc=ctypes.CDLL(None,use_errno=True)
            libc.acl_get_fd_np.argtypes=[ctypes.c_int,ctypes.c_int];libc.acl_get_fd_np.restype=ctypes.c_void_p
            libc.acl_free.argtypes=[ctypes.c_void_p];libc.acl_free.restype=ctypes.c_int
            ctypes.set_errno(0);acl=libc.acl_get_fd_np(stream.fileno(),0x100)
            if acl:
                libc.acl_free(acl);raise ValueError('credential ACL refused')
            if ctypes.get_errno()!=errno.ENOENT:raise ValueError('credential ACL inspection refused')
        else:raise ValueError('credential platform unsupported')
        raw=stream.read(limit+1)
        if len(raw)>limit:raise ValueError('credential size refused')
        return raw
