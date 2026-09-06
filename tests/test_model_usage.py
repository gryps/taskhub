import asyncio

from taskhub_v2.services.model_usage import ModelUsageReader


def test_plus_shows_five_hour_and_weekly_usage(monkeypatch):
    reader = ModelUsageReader("/codex", "http://proxy")

    async def snapshot(_home):
        return {"rateLimits": {
            "planType": "plus",
            "primary": {"usedPercent": 35, "windowDurationMins": 300, "resetsAt": 10},
            "secondary": {"usedPercent": 12, "windowDurationMins": 10080, "resetsAt": 20},
        }}

    monkeypatch.setattr(reader, "_account_snapshot", snapshot)
    usage = asyncio.run(reader.account("/plus", "plus"))
    assert usage["status"] == "available"
    assert [item["label"] for item in usage["metrics"]] == ["5 小时消耗", "周消耗"]
    assert [item["used_percent"] for item in usage["metrics"]] == [35, 12]


def test_pro_only_shows_weekly_usage(monkeypatch):
    reader = ModelUsageReader("/codex", "http://proxy")

    async def snapshot(_home):
        return {"rateLimitsByLimitId": {"codex": {
            "planType": "pro",
            "primary": {"usedPercent": 8, "windowDurationMins": 300},
            "secondary": {"usedPercent": 44, "windowDurationMins": 10080},
        }}}

    monkeypatch.setattr(reader, "_account_snapshot", snapshot)
    usage = asyncio.run(reader.account("/pro", "pro"))
    assert len(usage["metrics"]) == 1
    assert usage["metrics"][0]["label"] == "周消耗"
    assert usage["metrics"][0]["used_percent"] == 44


def test_api_balance_readers_return_remaining_only(monkeypatch):
    reader = ModelUsageReader("/codex", "http://proxy")

    def response(url, _key):
        if "deepseek" in url:
            return {"is_available": True, "balance_infos": [
                {"currency": "CNY", "total_balance": "42.50", "granted_balance": "2.50"}
            ]}
        return {"model_remains": [{"current_interval_remaining_percent": 91,
                                    "current_weekly_remaining_percent": 73}]}

    monkeypatch.setattr(reader, "_get_json", response)
    deepseek = asyncio.run(reader.deepseek_balance("secret", "https://api.deepseek.com/v1"))
    minimax = asyncio.run(reader.minimax_balance("secret"))
    assert deepseek["metrics"] == [{"label": "CNY", "value": "42.50", "unit": "CNY"}]
    assert minimax["metrics"] == [
        {"label": "5 小时剩余", "value": 91, "unit": "%"},
        {"label": "周剩余", "value": 73, "unit": "%"},
    ]
