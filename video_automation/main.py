"""Ponto de entrada: python main.py --audio narracao.mp3 --script roteiro.txt --out video_final.mp4"""

import sys

from broll_bot.cli import main

if __name__ == "__main__":
    sys.exit(main())
