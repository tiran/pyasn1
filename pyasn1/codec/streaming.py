#
# This file is part of pyasn1 software.
#
# Copyright (c) 2005-2019, Ilya Etingof <etingof@gmail.com>
# License: http://snmplabs.com/pyasn1/license.html
#
import io
import os
import sys

from pyasn1 import error
from pyasn1.type import univ

_PY2 = sys.version_info < (3,)


class CachingStreamWrapper(io.IOBase):
    """Wrapper around non-seekable streams.

    Note that the implementation is tied to the decoder,
    not checking for dangerous arguments for the sake
    of performance.

    The read bytes are kept in an internal cache until
    setting _markedPosition which may reset the cache.
    """
    def __init__(self, raw):
        self._raw = raw
        self._cache = io.BytesIO()
        self._markedPosition = 0

    def peek(self, n):
        result = self.read(n)
        self._cache.seek(-len(result), os.SEEK_CUR)
        return result

    def seekable(self):
        return True

    def seek(self, n=-1, whence=os.SEEK_SET):
        # Note that this not safe for seeking forward.
        return self._cache.seek(n, whence)

    def read(self, n=-1):
        read_from_cache = self._cache.read(n)
        if n != -1:
            n -= len(read_from_cache)
            if not n:  # 0 bytes left to read
                return read_from_cache

        read_from_raw = self._raw.read(n)

        self._cache.write(read_from_raw)

        return read_from_cache + read_from_raw

    @property
    def markedPosition(self):
        """Position where the currently processed element starts.

        This is used for back-tracking in SingleItemDecoder.__call__
        and (indefLen)ValueDecoder and should not be used for other purposes.
        The client is not supposed to ever seek before this position.
        """
        return self._markedPosition

    @markedPosition.setter
    def markedPosition(self, value):
        # By setting the value, we ensure we won't seek back before it.
        # `value` should be the same as the current position
        # We don't check for this for performance reasons.
        self._markedPosition = value

        # Whenever we set _marked_position, we know for sure
        # that we will not return back, and thus it is
        # safe to drop all cached data.
        if self._cache.tell() > io.DEFAULT_BUFFER_SIZE:
            self._cache = io.BytesIO(self._cache.read())
            self._markedPosition = 0

    def tell(self):
        return self._cache.tell()


def asSeekableStream(substrate):
    """Convert object to seekable byte-stream.

    Parameters
    ----------
    substrate: :py:class:`bytes` or :py:class:`io.IOBase` or :py:class:`univ.OctetString`

    Returns
    -------
    : :py:class:`io.IOBase`

    Raises
    ------
    : :py:class:`~pyasn1.error.PyAsn1Error`
        If the supplied substrate cannot be converted to a seekable stream.
    """
    if isinstance(substrate, io.BytesIO):
        return substrate

    elif isinstance(substrate, bytes):
        return io.BytesIO(substrate)

    elif isinstance(substrate, univ.OctetString):
        return io.BytesIO(substrate.asOctets())

    try:
        # Special case: impossible to set attributes on `file` built-in
        # XXX: broken, BufferedReader expects a "readable" attribute.
        if _PY2 and isinstance(substrate, file):
            return io.BufferedReader(substrate)

        elif substrate.seekable():  # Will fail for most invalid types
            return substrate

        else:
            return CachingStreamWrapper(substrate)

    except AttributeError:
        raise error.UnsupportedSubstrateError(
            "Cannot convert " + substrate.__class__.__name__ +
            " to a seekable bit stream.")


def isEndOfStream(substrate):
    """Check whether we have reached the end of a stream.

    Although it is more effective to read and catch exceptions, this
    function

    Parameters
    ----------
    substrate: :py:class:`IOBase`
        Stream to check

    Returns
    -------
    : :py:class:`bool`
    """
    if isinstance(substrate, io.BytesIO):
        cp = substrate.tell()
        substrate.seek(0, os.SEEK_END)
        result = substrate.tell() == cp
        substrate.seek(cp, os.SEEK_SET)
        yield result

    else:
        received = substrate.read(1)
        if received is None:
            yield

        if received:
            substrate.seek(-1, os.SEEK_CUR)

        yield not received


def peekIntoStream(substrate, size=-1):
    """Peek into stream.

    Parameters
    ----------
    substrate: :py:class:`IOBase`
        Stream to read from.

    size: :py:class:`int`
        How many bytes to peek (-1 = all available)

    Returns
    -------
    : :py:class:`bytes` or :py:class:`str`
        The return type depends on Python major version
    """
    if hasattr(substrate, "peek"):
        received = substrate.peek(size)
        if received is None:
            yield

        while len(received) < size:
            yield

        yield received

    else:
        current_position = substrate.tell()
        try:
            for chunk in readFromStream(substrate, size):
                yield chunk

        finally:
            substrate.seek(current_position)


def readFromStream(substrate, size=-1, context=None):
    """Read from the stream.

    Parameters
    ----------
    substrate: :py:class:`IOBase`
        Stream to read from.

    Keyword parameters
    ------------------
    size: :py:class:`int`
        How many bytes to read (-1 = all available)

    context: :py:class:`dict`
        Opaque caller context will be attached to exception objects created
        by this function.

    Yields
    ------
    : :py:class:`bytes` or :py:class:`str` or :py:class:`SubstrateUnderrunError`
        Read data or :py:class:`~pyasn1.error.SubstrateUnderrunError`
        object if no `size` bytes is readily available in the stream. The
        data type depends on Python major version

    Raises
    ------
    : :py:class:`~pyasn1.error.EndOfStreamError`
        Input stream is exhausted
    """
    while True:
        # this will block unless stream is non-blocking
        received = substrate.read(size)
        if received is None:  # non-blocking stream can do this
            yield error.SubstrateUnderrunError(context=context)

        elif not received and size != 0:  # end-of-stream
            raise error.EndOfStreamError(context=context)

        elif len(received) < size:
            substrate.seek(-len(received), os.SEEK_CUR)

            # behave like a non-blocking stream
            yield error.SubstrateUnderrunError(context=context)

        else:
            break

    yield received
