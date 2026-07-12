#!/usr/bin/env python3
"""Interactive REPL for the LSM-tree storage engine.

Usage:
    python cli.py [data_dir]

Commands:
    put <key> <value...>   store a value (value may contain spaces)
    get <key>               fetch a value, or "(nil)" if absent
    del <key>                delete a key
    range [start] [end]     list keys in [start, end] (either end optional)
    flush                    force the memtable to disk as a new SSTable
    compact                  force-merge all SSTables into one
    stats                    show memtable size / sstable file list
    exit                     quit
"""

import sys

from lsm.engine import LSMTree


def main() -> None:
    data_dir = sys.argv[1] if len(sys.argv) > 1 else "./lsm_data"
    engine = LSMTree(data_dir)
    print(f"lsm-tree engine ready at '{data_dir}' ('help' for commands)")

    try:
        while True:
            try:
                line = input("lsm> ").strip()
            except EOFError:
                break
            if not line:
                continue
            parts = line.split(" ")
            cmd = parts[0].lower()

            if cmd in ("exit", "quit"):
                break
            elif cmd == "help":
                print(__doc__)
            elif cmd == "put" and len(parts) >= 3:
                engine.put(parts[1], " ".join(parts[2:]))
                print("OK")
            elif cmd == "get" and len(parts) == 2:
                value = engine.get(parts[1])
                print(value if value is not None else "(nil)")
            elif cmd == "del" and len(parts) == 2:
                engine.delete(parts[1])
                print("OK")
            elif cmd == "range":
                start = parts[1] if len(parts) > 1 else None
                end = parts[2] if len(parts) > 2 else None
                count = 0
                for k, v in engine.range(start, end):
                    print(f"  {k} = {v}")
                    count += 1
                print(f"({count} entries)")
            elif cmd == "flush":
                path = engine.flush()
                print(f"flushed to {path}" if path else "memtable was empty")
            elif cmd == "compact":
                path = engine.compact()
                print(f"compacted to {path}" if path else "nothing to compact")
            elif cmd == "stats":
                print(engine.stats())
            else:
                print("unrecognized command, try 'help'")
    finally:
        engine.close()


if __name__ == "__main__":
    main()
