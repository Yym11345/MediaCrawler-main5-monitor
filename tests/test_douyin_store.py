from store.douyin import _pick_douyin_fans


def test_pick_douyin_fans_prefers_profile_display_count():
    user_info = {
        "follower_count": 1234,
        "mplatform_followers_count": 453000,
        "max_follower_count": 9999,
    }

    assert _pick_douyin_fans(user_info) == 453000


def test_pick_douyin_fans_falls_back_to_other_fields():
    assert _pick_douyin_fans({"follower_count": 1200, "max_follower_count": 9999}) == 1200
    assert _pick_douyin_fans({"max_follower_count": 9999}) == 9999
