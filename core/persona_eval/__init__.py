"""Phase 2.7 persona weight evaluation.

Public surface:
    data_loader.load_alerts_window(days)            -> list[NormalizedAlert]
    data_loader.score_for_persona(alerts, persona)  -> list[ScoredAlert]
    differentiation.jaccard_matrix(top_sets)        -> dict
    llm_judge.evaluate_persona(persona, top_alerts) -> JudgeResult
    report.render_markdown(eval_run)                -> str
    report.render_json(eval_run)                    -> dict
    run_log.log_run(...)                            -> int

Entry point: scripts/persona_eval.py.
"""
