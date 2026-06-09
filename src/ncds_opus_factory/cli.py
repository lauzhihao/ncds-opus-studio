"""统一 CLI 入口：python -m ncds_opus_factory {wst|tst|vid|asr|rw} [...args]。

也可以直接调子模块的 _cli：python -m ncds_opus_factory.commands.vid --prompt ...
"""

from __future__ import annotations

import sys


def main(argv: list[str] | None = None) -> int:
    args = list(argv if argv is not None else sys.argv[1:])
    if not args or args[0] in ("-h", "--help"):
        print("usage: nof {wst|tst|vid|asr|rw|shenkuo|guiguzi|liuyong|wudaozi|boya} [...args]")
        return 0
    cmd, rest = args[0], args[1:]
    if cmd == "wst":
        from ncds_opus_factory.commands import wst
        return wst._cli(rest)
    if cmd == "tst":
        from ncds_opus_factory.commands import tst
        return tst._cli(rest)
    if cmd == "vid":
        from ncds_opus_factory.commands import vid
        return vid._cli(rest)
    if cmd == "asr":
        from ncds_opus_factory.commands import asr
        return asr._cli(rest)
    if cmd == "rw":
        from ncds_opus_factory.commands import rw
        return rw._cli(rest)
    if cmd == "liuyong":
        from ncds_opus_factory.commands import liuyong
        return liuyong._cli(rest)
    if cmd == "guiguzi":
        from ncds_opus_factory.commands import guiguzi
        return guiguzi._cli(rest)
    if cmd == "shenkuo":
        from ncds_opus_factory.commands import shenkuo
        return shenkuo._cli(rest)
    if cmd == "wudaozi":
        from ncds_opus_factory.commands import wudaozi
        return wudaozi._cli(rest)
    if cmd == "boya":
        from ncds_opus_factory.commands import boya
        return boya._cli(rest)
    print(f"unknown command: {cmd}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
