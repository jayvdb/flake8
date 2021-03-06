"""Utility methods for flake8."""
import collections
import fnmatch as _fnmatch
import inspect
import io
import os
import re
import sys

DIFF_HUNK_REGEXP = re.compile(r'^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@.*$')


def parse_comma_separated_list(value):
    # type: (Union[Sequence[str], str]) -> List[str]
    """Parse a comma-separated list.

    :param value:
        String or list of strings to be parsed and normalized.
    :returns:
        List of values with whitespace stripped.
    :rtype:
        list
    """
    if not value:
        return []

    if not isinstance(value, (list, tuple)):
        value = value.split(',')

    return [item.strip() for item in value]


def normalize_paths(paths, parent=os.curdir):
    # type: (Union[Sequence[str], str], str) -> List[str]
    """Parse a comma-separated list of paths.

    :returns:
        The normalized paths.
    :rtype:
        [str]
    """
    return [normalize_path(p, parent)
            for p in parse_comma_separated_list(paths)]


def normalize_path(path, parent=os.curdir):
    # type: (str, str) -> str
    """Normalize a single-path.

    :returns:
        The normalized path.
    :rtype:
        str
    """
    # NOTE(sigmavirus24): Using os.path.sep allows for Windows paths to
    # be specified and work appropriately.
    separator = os.path.sep
    if separator in path:
        path = os.path.abspath(os.path.join(parent, path))
    return path.rstrip(separator)


def stdin_get_value():
    # type: () -> str
    """Get and cache it so plugins can use it."""
    cached_value = getattr(stdin_get_value, 'cached_stdin', None)
    if cached_value is None:
        stdin_value = sys.stdin.read()
        if sys.version_info < (3, 0):
            cached_type = io.BytesIO
        else:
            cached_type = io.StringIO
        stdin_get_value.cached_stdin = cached_type(stdin_value)
        cached_value = stdin_get_value.cached_stdin
    return cached_value.getvalue()


def parse_unified_diff(diff=None):
    # type: (str) -> List[str]
    """Parse the unified diff passed on stdin.

    :returns:
        dictionary mapping file names to sets of line numbers
    :rtype:
        dict
    """
    # Allow us to not have to patch out stdin_get_value
    if diff is None:
        diff = stdin_get_value()

    number_of_rows = None
    current_path = None
    parsed_paths = collections.defaultdict(set)
    for line in diff.splitlines():
        if number_of_rows:
            # NOTE(sigmavirus24): Below we use a slice because stdin may be
            # bytes instead of text on Python 3.
            if line[:1] != '-':
                number_of_rows -= 1
            # We're in the part of the diff that has lines starting with +, -,
            # and ' ' to show context and the changes made. We skip these
            # because the information we care about is the filename and the
            # range within it.
            # When number_of_rows reaches 0, we will once again start
            # searching for filenames and ranges.
            continue

        # NOTE(sigmavirus24): Diffs that we support look roughly like:
        #    diff a/file.py b/file.py
        #    ...
        #    --- a/file.py
        #    +++ b/file.py
        # Below we're looking for that last line. Every diff tool that
        # gives us this output may have additional information after
        # ``b/file.py`` which it will separate with a \t, e.g.,
        #    +++ b/file.py\t100644
        # Which is an example that has the new file permissions/mode.
        # In this case we only care about the file name.
        if line[:3] == '+++':
            current_path = line[4:].split('\t', 1)[0]
            # NOTE(sigmavirus24): This check is for diff output from git.
            if current_path[:2] == 'b/':
                current_path = current_path[2:]
            # We don't need to do anything else. We have set up our local
            # ``current_path`` variable. We can skip the rest of this loop.
            # The next line we will see will give us the hung information
            # which is in the next section of logic.
            continue

        hunk_match = DIFF_HUNK_REGEXP.match(line)
        # NOTE(sigmavirus24): pep8/pycodestyle check for:
        #    line[:3] == '@@ '
        # But the DIFF_HUNK_REGEXP enforces that the line start with that
        # So we can more simply check for a match instead of slicing and
        # comparing.
        if hunk_match:
            (row, number_of_rows) = [
                1 if not group else int(group)
                for group in hunk_match.groups()
            ]
            parsed_paths[current_path].update(
                range(row, row + number_of_rows)
            )

    # We have now parsed our diff into a dictionary that looks like:
    #    {'file.py': set(range(10, 16), range(18, 20)), ...}
    return parsed_paths


def is_windows():
    # type: () -> bool
    """Determine if we're running on Windows.

    :returns:
        True if running on Windows, otherwise False
    :rtype:
        bool
    """
    return os.name == 'nt'


def can_run_multiprocessing_on_windows():
    # type: () -> bool
    """Determine if we can use multiprocessing on Windows.

    :returns:
        True if the version of Python is modern enough, otherwise False
    :rtype:
        bool
    """
    is_new_enough_python27 = sys.version_info >= (2, 7, 11)
    is_new_enough_python3 = sys.version_info > (3, 2)
    return is_new_enough_python27 or is_new_enough_python3


def is_using_stdin(paths):
    # type: (List[str]) -> bool
    """Determine if we're going to read from stdin.

    :param list paths:
        The paths that we're going to check.
    :returns:
        True if stdin (-) is in the path, otherwise False
    :rtype:
        bool
    """
    return '-' in paths


def _default_predicate(*args):
    return False


def filenames_from(arg, predicate=None):
    # type: (str, callable) -> Generator
    """Generate filenames from an argument.

    :param str arg:
        Parameter from the command-line.
    :param callable predicate:
        Predicate to use to filter out filenames. If the predicate
        returns ``True`` we will exclude the filename, otherwise we
        will yield it. By default, we include every filename
        generated.
    :returns:
        Generator of paths
    """
    if predicate is None:
        predicate = _default_predicate
    if os.path.isdir(arg):
        for root, sub_directories, files in os.walk(arg):
            for filename in files:
                joined = os.path.join(root, filename)
                if predicate(joined):
                    continue
                yield joined
            # NOTE(sigmavirus24): os.walk() will skip a directory if you
            # remove it from the list of sub-directories.
            for directory in sub_directories:
                if predicate(directory):
                    sub_directories.remove(directory)
    else:
        yield arg


def fnmatch(filename, patterns, default=True):
    # type: (str, List[str], bool) -> bool
    """Wrap :func:`fnmatch.fnmatch` to add some functionality.

    :param str filename:
        Name of the file we're trying to match.
    :param list patterns:
        Patterns we're using to try to match the filename.
    :param bool default:
        The default value if patterns is empty
    :returns:
        True if a pattern matches the filename, False if it doesn't.
        ``default`` if patterns is empty.
    """
    if not patterns:
        return default
    return any(_fnmatch.fnmatch(filename, pattern) for pattern in patterns)


def parameters_for(plugin):
    # type: (flake8.plugins.manager.Plugin) -> List[str]
    """Return the parameters for the plugin.

    This will inspect the plugin and return either the function parameters
    if the plugin is a function or the parameters for ``__init__`` after
    ``self`` if the plugin is a class.

    :param plugin:
        The internal plugin object.
    :type plugin:
        flake8.plugins.manager.Plugin
    :returns:
        Parameters to the plugin.
    :rtype:
        list(str)
    """
    func = plugin.plugin
    is_class = not inspect.isfunction(func)
    if is_class:  # The plugin is a class
        func = plugin.plugin.__init__

    if sys.version_info < (3, 3):
        parameters = inspect.getargspec(func)[0]
    else:
        parameters = [
            parameter.name
            for parameter in inspect.signature(func).parameters.values()
            if parameter.kind == parameter.POSITIONAL_OR_KEYWORD
        ]

    if is_class:
        parameters.remove('self')

    return parameters
