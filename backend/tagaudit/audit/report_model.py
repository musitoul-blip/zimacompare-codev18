# report_model.py - F15: source unique du modele de rapport (Excel + HTML)
# Fonctions pures (aucun etat), consommees par excel_export et html_export.
import pandas as pd


def get_row_count(audit_results, data_key):
    data = audit_results.get(data_key)
    if isinstance(data, pd.DataFrame):
        return len(data)
    if data is not None and hasattr(data, '__len__'):
        try:
            return len(data)
        except TypeError:
            return 0
    return 0


def compute_health_score(audit_results, df_main, sheet_groups, health_weights):
    total = max(len(df_main) if df_main is not None else 0, 1)
    score = 100.0
    penalties = []
    key_to_label = {}
    for sheets in sheet_groups.values():
        for sheet_name, data_key in sheets:
            key_to_label[data_key] = sheet_name
    for data_key, weight in health_weights.items():
        count = get_row_count(audit_results, data_key)
        if count <= 0:
            continue
        raw = (count / total) * weight * 100
        penalty = min(raw, 15.0)
        score -= penalty
        label = key_to_label.get(data_key, data_key)
        penalties.append((label, penalty))
    final_score = max(0, int(round(score)))
    penalties.sort(key=lambda x: x[1], reverse=True)
    return final_score, penalties


def compute_top_issues(audit_results, sheet_groups, limit=5):
    excluded_groups = {'cockpit', 'donnees', 'kpi', 'informations'}  # T10 Lot C
    issues = []
    for group_name, sheets in sheet_groups.items():
        if group_name in excluded_groups:
            continue
        for sheet_name, data_key in sheets:
            count = get_row_count(audit_results, data_key)
            if count > 0:
                issues.append((sheet_name, count, group_name))
    issues.sort(key=lambda x: x[1], reverse=True)
    return issues[:limit]
