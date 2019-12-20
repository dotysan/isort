import re
from pathlib import Path
from typing import Any, NamedTuple, Optional, TextIO, Tuple

from . import output, parse
from .exceptions import (
    ExistingSyntaxErrors,
    FileSkipComment,
    FileSkipSetting,
    IntroducedSyntaxErrors,
    UnableToDetermineEncoding,
)
from .format import remove_whitespace, show_unified_diff
from .io import File
from .settings import DEFAULT_CONFIG, FILE_SKIP_COMMENT, Config

IMPORT_START_IDENTIFIERS = ("from ", "from.import", "import ", "import*")


def _config(
    path: Optional[Path] = None, config: Config = DEFAULT_CONFIG, **config_kwargs
) -> Config:
    if path:
        if (
            config is DEFAULT_CONFIG
            and "settings_path" not in config_kwargs
            and "settings_file" not in config_kwargs
        ):
            config_kwargs["settings_path"] = path

    if config_kwargs and config is not DEFAULT_CONFIG:
        raise ValueError(
            "You can either specify custom configuration options using kwargs or "
            "passing in a Config object. Not Both!"
        )
    elif config_kwargs:
        config = Config(**config_kwargs)

    return config


def sorted_imports(
    file_contents: str,
    extension: str = "py",
    config: Config = DEFAULT_CONFIG,
    file_path: Optional[Path] = None,
    disregard_skip: bool = False,
    **config_kwargs,
) -> str:
    config = _config(config=config, **config_kwargs)
    content_source = str(file_path or "Passed in content")
    if not disregard_skip:
        if FILE_SKIP_COMMENT in file_contents:
            raise FileSkipComment(content_source)

        elif file_path and config.is_skipped(file_path):
            raise FileSkipSetting(content_source)

    if config.atomic:
        try:
            compile(file_contents, content_source, "exec", 0, 1)
        except SyntaxError:
            raise ExistingSyntaxErrors(content_source)

    parsed_output = output.sorted_imports(
        parse.file_contents(file_contents, config=config), config, extension
    )
    if config.atomic:
        try:
            compile(file_contents, content_source, "exec", 0, 1)
        except SyntaxError:
            raise IntroducedSyntaxErrors(content_source)
    return parsed_output


def check_imports(
    file_contents: str,
    show_diff: bool = False,
    extension: str = "py",
    config: Config = DEFAULT_CONFIG,
    file_path: Optional[Path] = None,
    disregard_skip: bool = False,
    **config_kwargs,
) -> bool:
    config = _config(config=config, **config_kwargs)

    sorted_output = sorted_imports(
        file_contents=file_contents,
        extension=extension,
        config=config,
        file_path=file_path,
        disregard_skip=disregard_skip,
        **config_kwargs,
    )
    if config.ignore_whitespace:
        line_separator = config.line_ending or parse._infer_line_separator(file_contents)
        compare_in = remove_whitespace(file_contents, line_separator=line_separator).strip()
        compare_out = remove_whitespace(sorted_output, line_separator=line_separator).strip()
    else:
        compare_in = file_contents.strip()
        compare_out = sorted_output.strip()

    if compare_out == compare_in:
        if config.verbose:
            print(f"SUCCESS: {file_path or ''} Everything Looks Good!")
        return True
    else:
        print(f"ERROR: {file_path or ''} Imports are incorrectly sorted.")
        if show_diff:
            show_unified_diff(
                file_input=file_contents, file_output=sorted_output, file_path=file_path
            )
        return False


def sorted_file(filename: str, config: Config = DEFAULT_CONFIG, **config_kwargs) -> str:
    file_data = File.read(filename)
    config = _config(path=file_data.path.parent, config=config)
    return sorted_imports(
        file_contents=file_data.contents,
        extension=file_data.extension,
        config=config,
        file_path=file_data.path,
        **config_kwargs,
    )


def sort_imports(
    input_stream: TextIO,
    output_stream: TextIO,
    extension: str = "py",
    config: Config = DEFAULT_CONFIG,
) -> None:
    """Parses stream identifying sections of contiguous imports and sorting them

    Code with unsorted imports is read from the provided `input_stream`, sorted and then
    outputted to the specified output_stream.

    - `input_stream`: Text stream with unsorted import sections.
    - `output_stream`: Text stream to output sorted inputs into.
    - `config`: Config settings to use when sorting imports. Defaults settings.DEFAULT_CONFIG.
    """
    import_section: str = ""
    in_quote: str = ""
    first_comment_index_start: int = -1
    first_comment_index_end: int = -1
    contains_imports: bool = False
    in_top_comment: bool = False
    section_comments = [f"# {heading}" for heading in config.import_headings.values()]
    for index, line in enumerate(input_stream):
        if index == 1 and line.startswith("#"):
            in_top_comment = True
        elif in_top_comment:
            if not line.startswith("#") or line in section_comments:
                in_top_comment = False
                first_comment_index_end = index - 1
        elif '"' in line or "'" in line:
            char_index = 0
            if first_comment_index_start == -1 and (line.startswith('"') or line.startswith("'")):
                first_comment_index_start = index
            while char_index < len(line):
                if line[char_index] == "\\":
                    char_index += 1
                elif in_quote:
                    if line[char_index : char_index + len(in_quote)] == in_quote:
                        in_quote = ""
                        if first_comment_index_end < first_comment_index_start:
                            first_comment_index_end = index
                elif line[char_index] in ("'", '"'):
                    long_quote = line[char_index : char_index + 3]
                    if long_quote in ('"""', "'''"):
                        in_quote = long_quote
                        char_index += 2
                    else:
                        in_quote = line[char_index]
                elif line[char_index] == "#":
                    break
                char_index += 1

        not_imports = bool(in_quote) or in_top_comment
        if not in_quote:
            stripped_line = line.strip()
            if not stripped_line or stripped_line.startswith("#"):
                import_section += line
            elif stripped_line.startswith(IMPORT_START_IDENTIFIERS):
                import_section += line
                if "(" in stripped_line and ")" not in stripped_line:
                    nested_line = line
                    nested_stripped_line = nested_line.strip().split("#")[0]
                    while ")" not in nested_stripped_line:
                        nested_line = input_stream.readline()
                        nested_stripped_line = nested_line.strip()
                        import_section += nested_line

                if stripped_line.endswith("\\"):
                    nested_line = line
                    nested_stripped_line = nested_line.strip()
                    while nested_line and nested_stripped_line.endswith("\\"):
                        nested_line = input_stream.readline()
                        nested_stripped_line = nested_line.strip()
                        import_section += nested_line

                contains_imports = True
            else:
                not_imports = True

        if not_imports:
            if import_section:
                import_section += line
                if not contains_imports:
                    output_stream.write(import_section)
                else:
                    output_stream.write(
                        output.sorted_imports(
                            parse.file_contents(import_section, config=config), config, extension
                        )
                    )
                contains_imports = False
                import_section = ""
            else:
                output_stream.write(line)
                not_imports = False
