"""factory CLI 入口：``python -m ncds_opus_factory {asr|<agent>} [...args]``（``nof``）。

agent 子命令：asr + guiguzi/liuyong/wudaozi/boya/shenkuo/wolong。
core primitive（wst/tst/vid）已拆到 ``nof-core``（见 ncds_opus_core.cli，§9.4 二分）。

也可以直接调子模块的 _cli：python -m ncds_opus_factory.commands.asr ...
"""

from __future__ import annotations

import sys


def main(argv: list[str] | None = None) -> int:
    args = list(argv if argv is not None else sys.argv[1:])
    if not args or args[0] in ("-h", "--help"):
        print("usage: nof {asr|shenkuo|guiguzi|liuyong|wudaozi|boya|wolong} [...args]")
        print("  (primitives wst/tst/vid 已移至 `nof-core`)")
        return 0
    cmd, rest = args[0], args[1:]
    if cmd == "asr":
        from ncds_opus_factory.commands import asr
        return asr._cli(rest)
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
    if cmd == "wolong":
        from ncds_opus_factory.commands import wolong
        return wolong._cli(rest)
    print(f"unknown command: {cmd}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
