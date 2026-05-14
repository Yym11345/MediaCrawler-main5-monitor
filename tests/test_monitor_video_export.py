from api.routers.monitor import _normalize_video_id


def test_bili_bv_export_id_normalizes_to_aid():
    assert _normalize_video_id("bili", "BV17x411w7KC") == "170001"
    assert _normalize_video_id("bili", "https://www.bilibili.com/video/BV17x411w7KC") == "170001"
    assert _normalize_video_id("bili", "BV12ukRYCEz9") == "113706256046207"
    assert (
        _normalize_video_id(
            "bili",
            "https://www.bilibili.com/video/BV12ukRYCEz9/?spm_id_from=333.337.search-card.all.click",
        )
        == "113706256046207"
    )


def test_bili_av_export_id_stays_numeric():
    assert _normalize_video_id("bili", "https://www.bilibili.com/video/av786788972") == "786788972"
