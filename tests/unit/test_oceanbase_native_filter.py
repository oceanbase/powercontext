from powermem.utils.oceanbase_util import OceanBaseUtil


class _Columns(dict):
    pass


class _Table:
    c = _Columns({"created_at": object(), "user_id": object()})


class _Model:
    __table__ = _Table()


def test_native_hybrid_filter_accepts_dollar_prefixed_range_ops():
    filters = {"created_at": {"$gt": "2026-01-01T00:00:00Z"}}

    native_filters = OceanBaseUtil.convert_filters_to_native_format(
        filters, _Model
    )

    assert native_filters == [
        {"range": {"created_at": {"gt": "2026-01-01T00:00:00Z"}}}
    ]


def test_native_hybrid_filter_accepts_dollar_prefixed_match_ops():
    filters = {"user_id": {"$in": ["u1", "u2"]}}

    native_filters = OceanBaseUtil.convert_filters_to_native_format(
        filters, _Model
    )

    assert native_filters == [
        {
            "bool": {
                "should": [
                    {"term": {"user_id": "u1"}},
                    {"term": {"user_id": "u2"}},
                ]
            }
        }
    ]
