"""core primitive CLI 入口：``nof-core {wst|tst|vid} [...args]``。

只分发**有命令行用例**的 primitive：wst/tst/vid。
render/render_015/tts 也是 core primitive，但**仅经 server 暴露**（无 CLI 分支，
与拆分前 ncds_opus_factory.cli 的行为一致）。

agent 子命令（asr + guiguzi/...）在 factory：``nof {asr|...}``。
"""

from __future__ import annotations

import sys

_PRIMITIVES = ("wst", "tst", "vid")


def main(argv: list[str] | None = None) -> int:
    args = list(argv if argv is not None else sys.argv[1:])
    if not args or args[0] in ("-h", "--help"):
        print(f"usage: nof-core {{{'|'.join(_PRIMITIVES)}}} [...args]")
        return 0
    cmd, rest = args[0], args[1:]
    if cmd == "wst":
        from ncds_opus_core.commands import wst
        return wst._cli(rest)
    if cmd == "tst":
        from ncds_opus_core.commands import tst
        return tst._cli(rest)
    if cmd == "vid":
        from ncds_opus_core.commands import vid
        return vid._cli(rest)
    print(f"unknown command: {cmd}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
