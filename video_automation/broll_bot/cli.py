"""Linha de comando: python main.py --audio narracao.mp3 --script roteiro.txt --out video_final.mp4"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

from .config import load_config
from .log import get_logger, setup_logging
from .pipeline import RunOptions, run_pipeline


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Gera um vídeo com b-rolls sincronizados à narração (sem legendas).")
    p.add_argument("--audio", required=True, type=Path, help="narração em .mp3 (ou outro formato de áudio)")
    p.add_argument("--script", required=True, type=Path, help="roteiro em .txt ou .md")
    p.add_argument("--out", default=Path("video_final.mp4"), type=Path, help="arquivo de saída (padrão: video_final.mp4)")
    p.add_argument("--config", default=Path("config.yaml"), type=Path, help="arquivo de configuração")
    p.add_argument("--env", default=Path(".env"), type=Path, help="arquivo .env com as chaves das APIs")
    p.add_argument("--seed", type=int, help="sobrescreve a seed (resultado reproduzível)")
    p.add_argument("--no-llm", action="store_true", help="usa a análise heurística em vez do Claude")
    p.add_argument("--no-clip", action="store_true", help="desativa o ranking visual com CLIP")
    p.add_argument("--no-transcribe", action="store_true", help="não transcreve; estima o tempo das frases")
    p.add_argument("--reindex-library", action="store_true", help="reindexa a biblioteca local (Mixkit)")
    p.add_argument("--keep-temp", action="store_true", help="mantém os arquivos intermediários")
    p.add_argument("--report", type=Path, help="caminho do report.json (padrão: ao lado do vídeo)")
    p.add_argument("--log-level", default="INFO", choices=["DEBUG", "INFO", "WARNING", "ERROR"])
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    base_dir = Path.cwd()
    cfg = load_config(args.config, args.env)
    if args.seed is not None:
        cfg.seed = args.seed
    if args.no_llm:
        cfg.analysis.provider = "heuristic"
    if args.no_clip:
        cfg.ranking.use_clip = False
    if args.no_transcribe:
        cfg.transcription.enabled = False
    if args.keep_temp:
        cfg.render.keep_intermediates = True

    run_id = time.strftime("%Y%m%d-%H%M%S")
    work_dir = base_dir / cfg.paths.work_dir / run_id
    setup_logging(args.log_level, work_dir / "pipeline.jsonl")
    log = get_logger("cli")

    for label, path in (("áudio", args.audio), ("roteiro", args.script)):
        if not path.exists():
            log.error(f"{label} não encontrado: {path}", extra={"stage": "input"})
            return 2

    opts = RunOptions(audio=args.audio, script=args.script, output=args.out, base_dir=base_dir,
                      reindex_library=args.reindex_library, report_path=args.report)
    try:
        report = run_pipeline(cfg, opts, work_dir)
    except KeyboardInterrupt:
        log.error("interrompido pelo usuário", extra={"stage": "cli"})
        return 130
    except Exception as exc:  # noqa: BLE001 - mensagem clara no topo
        log.exception(f"falha no pipeline: {exc}", extra={"stage": "cli"})
        return 1
    totals = report["totals"]
    print(
        f"\n✔ {args.out}  ({report['video_duration']:.3f}s, {totals['scenes']} cenas, {totals['shots']} b-rolls, "
        f"{totals['fallbacks']} fallbacks, requisições: {totals['api_requests']})"
    )
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
