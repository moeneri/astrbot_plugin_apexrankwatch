import asyncio
import json
from datetime import datetime, timezone

import httpx
import pytest

import apex_service


def _client_with_pages(
    pages: dict[str, str],
    requested_urls: list[str] | None = None,
):
    client = object.__new__(apex_service.ApexApiClient)
    client._season_cache_ttl_seconds = 1800
    client._season_cache = {}
    client._season_lock = asyncio.Lock()
    client._split_index_cache = None

    async def request_text(url: str, **_kwargs) -> str:
        if requested_urls is not None:
            requested_urls.append(url)
        result = pages[url]
        if isinstance(result, Exception):
            raise result
        return result

    client._request_text_with_retry = request_text
    return client


class _SilentLogger:
    def __getattr__(self, _name):
        return lambda *_args, **_kwargs: None


def _http_status_error(url: str, status_code: int) -> httpx.HTTPStatusError:
    request = httpx.Request("GET", url)
    response = httpx.Response(status_code, request=request)
    return httpx.HTTPStatusError(
        f"HTTP {status_code}",
        request=request,
        response=response,
    )


def _season_with_range(start_iso: str, end_iso: str):
    return apex_service.SeasonInfo(
        season_number=29,
        season_name="Overclocked",
        start_date="test",
        end_date="test",
        timezone="UTC",
        update_time_hint="test",
        source="apexseasons.online",
        season_url="https://apexseasons.online/seasons/season-29/",
        start_iso=start_iso,
        end_iso=end_iso,
    )


def test_current_season_uses_complete_apexseasons_range():
    detail_url = "https://apexseasons.online/seasons/season-29-overclocked/"
    home_html = f'''<script type="application/ld+json">{{
      "@context": "https://schema.org",
      "@type": "ItemList",
      "itemListElement": [{{
        "@type": "ListItem",
        "position": 1,
        "name": "Season 29 Overclocked",
        "url": "{detail_url}"
      }}]
    }}</script>'''
    detail_html = '''<script type="application/ld+json">{
      "@context": "https://schema.org",
      "@type": "Event",
      "name": "Apex Legends Season 29 Overclocked",
      "startDate": "2026-05-05T18:00:00Z",
      "endDate": "2026-08-04T18:00:00Z"
    }</script>'''
    client = _client_with_pages(
        {
            apex_service.APEX_SEASONS_HOME_URL: home_html,
            detail_url: detail_html,
        }
    )

    season = asyncio.run(
        client.fetch_current_season_info(
            now=datetime(2026, 7, 10, tzinfo=timezone.utc),
        )
    )

    assert season.season_number == 29
    assert season.season_name == "Overclocked"
    assert season.start_iso == "2026-05-05T18:00:00Z"
    assert season.end_iso == "2026-08-04T18:00:00Z"
    assert season.end_iso != "2026-09-22T17:00:00Z"
    assert season.source == "apexseasons.online"
    assert season.supports_ranked_splits is True
    assert season.split_source == "推导"
    assert all(split_info.exact is False for split_info in season.splits)
    assert season.splits[1].start_iso == "2026-06-23T17:00:00Z"


def test_private_current_fetch_accepts_injected_now():
    detail_url = "https://apexseasons.online/seasons/season-29-overclocked/"
    home_html = f'''<script type="application/ld+json">{{
      "@type": "ItemList",
      "itemListElement": [{{
        "position": 1,
        "name": "Season 29 Overclocked",
        "url": "{detail_url}"
      }}]
    }}</script>'''
    detail_html = '''<script type="application/ld+json">{
      "@type": "Event",
      "startDate": "2026-05-05T18:00:00Z",
      "endDate": "2026-08-04T18:00:00Z"
    }</script>'''
    client = _client_with_pages(
        {
            apex_service.APEX_SEASONS_HOME_URL: home_html,
            detail_url: detail_html,
        }
    )

    season = asyncio.run(
        client._fetch_season_from_apexseasons(
            None,
            use_public_split_index=False,
            now=datetime(2026, 6, 10, tzinfo=timezone.utc),
        )
    )

    assert season.season_number == 29
    assert season.status_text == "进行中"
    assert season.current_split_label == "上半赛季"


def test_current_fetch_skips_upcoming_first_reference():
    upcoming_url = "https://apexseasons.online/seasons/season-30/"
    current_url = "https://apexseasons.online/seasons/season-29/"
    home_html = f'''<script type="application/ld+json">{{
      "@type": "ItemList",
      "itemListElement": [
        {{
          "position": 1,
          "name": "Season 30 Future Shock",
          "url": "{upcoming_url}"
        }},
        {{
          "position": 2,
          "name": "Season 29 Overclocked",
          "url": "{current_url}"
        }}
      ]
    }}</script>'''
    client = _client_with_pages(
        {
            apex_service.APEX_SEASONS_HOME_URL: home_html,
            upcoming_url: '''<script type="application/ld+json">{
              "@type": "Event",
              "startDate": "2026-08-04T18:00:00Z",
              "endDate": "2026-11-03T18:00:00Z"
            }</script>''',
            current_url: '''<script type="application/ld+json">{
              "@type": "Event",
              "startDate": "2026-05-05T18:00:00Z",
              "endDate": "2026-08-04T18:00:00Z"
            }</script>''',
        }
    )

    season = asyncio.run(
        client._fetch_season_from_apexseasons(
            None,
            use_public_split_index=False,
            now=datetime(2026, 7, 10, tzinfo=timezone.utc),
        )
    )

    assert season.season_number == 29


def test_current_fetch_skips_stale_first_reference():
    stale_url = "https://apexseasons.online/seasons/season-28/"
    current_url = "https://apexseasons.online/seasons/season-29/"
    home_html = f'''<script type="application/ld+json">{{
      "@type": "ItemList",
      "itemListElement": [
        {{
          "position": 1,
          "name": "Season 28 Breach",
          "url": "{stale_url}"
        }},
        {{
          "position": 2,
          "name": "Season 29 Overclocked",
          "url": "{current_url}"
        }}
      ]
    }}</script>'''
    client = _client_with_pages(
        {
            apex_service.APEX_SEASONS_HOME_URL: home_html,
            stale_url: '''<script type="application/ld+json">{
              "@type": "Event",
              "startDate": "2026-02-03T18:00:00Z",
              "endDate": "2026-05-05T18:00:00Z"
            }</script>''',
            current_url: '''<script type="application/ld+json">{
              "@type": "Event",
              "startDate": "2026-05-05T18:00:00Z",
              "endDate": "2026-08-04T18:00:00Z"
            }</script>''',
        }
    )

    season = asyncio.run(
        client._fetch_season_from_apexseasons(
            None,
            use_public_split_index=False,
            now=datetime(2026, 7, 10, tzinfo=timezone.utc),
        )
    )

    assert season.season_number == 29


def test_current_fetch_stops_after_first_expired_newest_candidate():
    older_27_url = "https://apexseasons.online/seasons/season-27/"
    older_28_url = "https://apexseasons.online/seasons/season-28/"
    expired_url = "https://apexseasons.online/seasons/season-29/"
    upcoming_url = "https://apexseasons.online/seasons/season-30/"
    home_html = f'''<script type="application/ld+json">{{
      "@type": "ItemList",
      "itemListElement": [
        {{"position": 1, "name": "Season 27 From the Rift", "url": "{older_27_url}"}},
        {{"position": 2, "name": "Season 28 Breach", "url": "{older_28_url}"}},
        {{"position": 3, "name": "Season 29 Overclocked", "url": "{expired_url}"}},
        {{"position": 4, "name": "Season 30 Future Shock", "url": "{upcoming_url}"}}
      ]
    }}</script>'''
    pages = {
        apex_service.APEX_SEASONS_HOME_URL: home_html,
        older_27_url: '''<script type="application/ld+json">{
          "@type": "Event",
          "startDate": "2025-11-04T18:00:00Z",
          "endDate": "2026-02-03T18:00:00Z"
        }</script>''',
        older_28_url: '''<script type="application/ld+json">{
          "@type": "Event",
          "startDate": "2026-02-03T18:00:00Z",
          "endDate": "2026-05-05T18:00:00Z"
        }</script>''',
        expired_url: '''<script type="application/ld+json">{
          "@type": "Event",
          "startDate": "2026-05-05T18:00:00Z",
          "endDate": "2026-08-04T18:00:00Z"
        }</script>''',
        upcoming_url: '''<script type="application/ld+json">{
          "@type": "Event",
          "startDate": "2026-08-10T18:00:00Z",
          "endDate": "2026-11-10T18:00:00Z"
        }</script>''',
    }
    requested_urls: list[str] = []
    client = _client_with_pages(pages, requested_urls)

    try:
        asyncio.run(
            client._fetch_season_from_apexseasons(
                None,
                use_public_split_index=False,
                now=datetime(2026, 8, 6, tzinfo=timezone.utc),
            )
        )
    except RuntimeError as exc:
        assert "完整起止时间" in str(exc)
    else:
        raise AssertionError("a gap between seasons must not select a range")

    assert requested_urls == [
        apex_service.APEX_SEASONS_HOME_URL,
        upcoming_url,
        expired_url,
    ]


@pytest.mark.parametrize(
    "unsafe_url",
    [
        "http://169.254.169.254/latest/meta-data/",
        "https://example.com/seasons/season-30/",
        "https://user:password@apexseasons.online/seasons/season-30/",
        "https://apexseasons.online:8443/seasons/season-30/",
    ],
)
def test_current_fetch_rejects_unsafe_detail_url_before_request(unsafe_url):
    home_html = f'''<script type="application/ld+json">{json.dumps({
      "@type": "ItemList",
      "itemListElement": [{
        "position": 1,
        "name": "Season 30 Future Shock",
        "url": unsafe_url,
      }],
    })}</script>'''
    requested_urls: list[str] = []
    detail_html = '''<script type="application/ld+json">{
      "@type": "Event",
      "startDate": "2026-05-05T18:00:00Z",
      "endDate": "2026-08-04T18:00:00Z"
    }</script>'''
    client = _client_with_pages(
        {
            apex_service.APEX_SEASONS_HOME_URL: home_html,
            unsafe_url: detail_html,
        },
        requested_urls,
    )

    with pytest.raises(RuntimeError, match="URL"):
        asyncio.run(
            client._fetch_season_from_apexseasons(
                None,
                use_public_split_index=False,
                now=datetime(2026, 7, 10, tzinfo=timezone.utc),
            )
        )

    assert requested_urls == [apex_service.APEX_SEASONS_HOME_URL]


def test_current_fetch_validates_homepage_before_request(monkeypatch):
    unsafe_home = "http://169.254.169.254/latest/meta-data/"
    monkeypatch.setattr(apex_service, "APEX_SEASONS_HOME_URL", unsafe_home)
    requested_urls: list[str] = []
    client = _client_with_pages(
        {unsafe_home: "<html>not a season page</html>"},
        requested_urls,
    )

    with pytest.raises(RuntimeError, match="URL"):
        asyncio.run(
            client._fetch_season_from_apexseasons(
                None,
                use_public_split_index=False,
                now=datetime(2026, 7, 10, tzinfo=timezone.utc),
            )
        )

    assert requested_urls == []


def test_season_text_request_rejects_cross_origin_redirect_before_target_request():
    requested_urls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested_urls.append(str(request.url))
        if request.url.host == "apexseasons.online":
            return httpx.Response(
                302,
                headers={"Location": "https://example.com/redirect-target"},
                request=request,
            )
        raise AssertionError("cross-origin redirect target must not be requested")

    async def exercise():
        client = apex_service.ApexApiClient("", 1000, 0, _SilentLogger())
        await client._client.aclose()
        client._client = httpx.AsyncClient(
            transport=httpx.MockTransport(handler),
            follow_redirects=True,
        )
        try:
            with pytest.raises(RuntimeError, match="URL"):
                await client._request_text_with_retry(
                    "https://apexseasons.online/start",
                    allowed_hosts=apex_service.APEX_SEASONS_ALLOWED_HOSTS,
                )
        finally:
            await client.close()

    asyncio.run(exercise())

    assert requested_urls == ["https://apexseasons.online/start"]


def test_season_text_request_rejects_nondefault_port_redirect_before_target_request():
    requested_urls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested_urls.append(str(request.url))
        if str(request.url) == "https://apexseasons.online/start":
            return httpx.Response(
                302,
                headers={"Location": "https://apexseasons.online:8443/detail"},
                request=request,
            )
        raise AssertionError("non-default port redirect target must not be requested")

    async def exercise():
        client = apex_service.ApexApiClient("", 1000, 0, _SilentLogger())
        await client._client.aclose()
        client._client = httpx.AsyncClient(
            transport=httpx.MockTransport(handler),
            follow_redirects=True,
        )
        try:
            with pytest.raises(RuntimeError, match="URL"):
                await client._request_text_with_retry(
                    "https://apexseasons.online/start",
                    allowed_hosts=apex_service.APEX_SEASONS_ALLOWED_HOSTS,
                )
        finally:
            await client.close()

    asyncio.run(exercise())

    assert requested_urls == ["https://apexseasons.online/start"]


def test_season_text_request_allows_same_origin_https_redirect():
    requested_urls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested_urls.append(str(request.url))
        if str(request.url) == "https://apexseasons.online/start":
            return httpx.Response(
                302,
                headers={"Location": "https://www.apexseasons.online/detail"},
                request=request,
            )
        if str(request.url) == "https://www.apexseasons.online/detail":
            return httpx.Response(200, text="season detail", request=request)
        raise AssertionError(f"unexpected URL: {request.url}")

    async def exercise():
        client = apex_service.ApexApiClient("", 1000, 0, _SilentLogger())
        await client._client.aclose()
        client._client = httpx.AsyncClient(
            transport=httpx.MockTransport(handler),
            follow_redirects=True,
        )
        try:
            return await client._request_text_with_retry(
                "https://apexseasons.online/start",
                allowed_hosts=apex_service.APEX_SEASONS_ALLOWED_HOSTS,
            )
        finally:
            await client.close()

    assert asyncio.run(exercise()) == "season detail"
    assert requested_urls == [
        "https://apexseasons.online/start",
        "https://www.apexseasons.online/detail",
    ]


def test_current_fetch_caps_invalid_candidate_requests():
    references = [
        {
            "position": index + 1,
            "name": f"Season {54 - index} Invalid",
            "url": f"https://apexseasons.online/seasons/season-{54 - index}/",
        }
        for index in range(25)
    ]
    home_html = (
        '<script type="application/ld+json">'
        + json.dumps({"@type": "ItemList", "itemListElement": references})
        + "</script>"
    )
    pages = {apex_service.APEX_SEASONS_HOME_URL: home_html}
    pages.update({reference["url"]: "<html>invalid range</html>" for reference in references})
    requested_urls: list[str] = []
    client = _client_with_pages(pages, requested_urls)

    with pytest.raises(RuntimeError, match="完整起止时间"):
        asyncio.run(
            client._fetch_season_from_apexseasons(
                None,
                use_public_split_index=False,
                now=datetime(2026, 7, 10, tzinfo=timezone.utc),
            )
        )

    assert len(requested_urls) <= 1 + 4
    assert requested_urls[0] == apex_service.APEX_SEASONS_HOME_URL


def test_current_fetch_skips_future_404_and_selects_current_range():
    upcoming_url = "https://apexseasons.online/seasons/season-30/"
    current_url = "https://apexseasons.online/seasons/season-29/"
    home_html = f'''<script type="application/ld+json">{json.dumps({
      "@type": "ItemList",
      "itemListElement": [
        {"position": 1, "name": "Season 30 Future Shock", "url": upcoming_url},
        {"position": 2, "name": "Season 29 Overclocked", "url": current_url},
      ],
    })}</script>'''
    current_html = '''<script type="application/ld+json">{
      "@type": "Event",
      "startDate": "2026-05-05T18:00:00Z",
      "endDate": "2026-08-04T18:00:00Z"
    }</script>'''
    requested_urls: list[str] = []
    client = _client_with_pages(
        {
            apex_service.APEX_SEASONS_HOME_URL: home_html,
            upcoming_url: _http_status_error(upcoming_url, 404),
            current_url: current_html,
        },
        requested_urls,
    )

    season = asyncio.run(
        client._fetch_season_from_apexseasons(
            None,
            use_public_split_index=False,
            now=datetime(2026, 7, 10, tzinfo=timezone.utc),
        )
    )

    assert season.season_number == 29
    assert requested_urls == [
        apex_service.APEX_SEASONS_HOME_URL,
        upcoming_url,
        current_url,
    ]


def test_current_fetch_propagates_general_candidate_request_error():
    upcoming_url = "https://apexseasons.online/seasons/season-30/"
    current_url = "https://apexseasons.online/seasons/season-29/"
    home_html = f'''<script type="application/ld+json">{json.dumps({
      "@type": "ItemList",
      "itemListElement": [
        {"position": 1, "name": "Season 30 Future Shock", "url": upcoming_url},
        {"position": 2, "name": "Season 29 Overclocked", "url": current_url},
      ],
    })}</script>'''
    request = httpx.Request("GET", upcoming_url)
    requested_urls: list[str] = []
    client = _client_with_pages(
        {
            apex_service.APEX_SEASONS_HOME_URL: home_html,
            upcoming_url: httpx.ConnectError("network unavailable", request=request),
        },
        requested_urls,
    )

    with pytest.raises(httpx.ConnectError, match="network unavailable"):
        asyncio.run(
            client._fetch_season_from_apexseasons(
                None,
                use_public_split_index=False,
                now=datetime(2026, 7, 10, tzinfo=timezone.utc),
            )
        )

    assert requested_urls == [apex_service.APEX_SEASONS_HOME_URL, upcoming_url]


def test_split_boundary_is_first_beijing_wednesday_not_before_midpoint():
    start = datetime(2026, 5, 5, 18, tzinfo=timezone.utc)
    end = datetime(2026, 8, 4, 18, tzinfo=timezone.utc)

    boundary = apex_service._infer_split_boundary(start, end)

    assert boundary == datetime(2026, 6, 23, 17, tzinfo=timezone.utc)


def test_split_boundary_keeps_midpoint_when_it_is_wednesday_0100_beijing():
    start = datetime(2026, 6, 2, 17, tzinfo=timezone.utc)
    end = datetime(2026, 6, 16, 17, tzinfo=timezone.utc)

    boundary = apex_service._infer_split_boundary(start, end)

    assert boundary == datetime(2026, 6, 9, 17, tzinfo=timezone.utc)


def test_split_boundary_does_not_choose_a_closer_wednesday_before_midpoint():
    start = datetime(2026, 6, 3, 17, tzinfo=timezone.utc)
    end = datetime(2026, 6, 17, 17, tzinfo=timezone.utc)

    boundary = apex_service._infer_split_boundary(start, end)

    assert boundary == datetime(2026, 6, 16, 17, tzinfo=timezone.utc)


def test_invalid_complete_range_is_rejected_without_fixed_91_day_fallback():
    detail_url = "https://apexseasons.online/seasons/season-29-overclocked/"
    home_html = f'''<script type="application/ld+json">{{
      "@context": "https://schema.org",
      "@type": "ItemList",
      "itemListElement": [{{
        "@type": "ListItem",
        "position": 1,
        "name": "Season 29 Overclocked",
        "url": "{detail_url}"
      }}]
    }}</script>'''
    client = _client_with_pages(
        {
            apex_service.APEX_SEASONS_HOME_URL: home_html,
            detail_url: "<html>no complete event range</html>",
        }
    )

    try:
        asyncio.run(client.fetch_current_season_info())
    except RuntimeError as exc:
        assert "完整起止时间" in str(exc)
    else:
        raise AssertionError("missing complete range must fail")


def test_reversed_complete_range_is_rejected():
    detail_url = "https://apexseasons.online/seasons/season-29-overclocked/"
    home_html = f'''<script type="application/ld+json">{{
      "@type": "ItemList",
      "itemListElement": [{{
        "position": 1,
        "name": "Season 29 Overclocked",
        "url": "{detail_url}"
      }}]
    }}</script>'''
    detail_html = '''<script type="application/ld+json">{
      "@type": "Event",
      "startDate": "2026-08-04T18:00:00Z",
      "endDate": "2026-05-05T18:00:00Z"
    }</script>'''
    client = _client_with_pages(
        {
            apex_service.APEX_SEASONS_HOME_URL: home_html,
            detail_url: detail_html,
        }
    )

    try:
        asyncio.run(client.fetch_current_season_info())
    except RuntimeError as exc:
        assert "完整起止时间" in str(exc)
    else:
        raise AssertionError("reversed complete range must fail")


def test_complete_range_rejects_date_only_values():
    season = _season_with_range("2026-05-05", "2026-08-04")

    try:
        apex_service._require_complete_season_range(season)
    except RuntimeError as exc:
        assert "完整起止时间" in str(exc)
    else:
        raise AssertionError("date-only complete range must fail")


def test_complete_range_rejects_offset_free_datetimes():
    season = _season_with_range(
        "2026-05-05T18:00:00",
        "2026-08-04T18:00:00",
    )

    try:
        apex_service._require_complete_season_range(season)
    except RuntimeError as exc:
        assert "完整起止时间" in str(exc)
    else:
        raise AssertionError("offset-free complete range must fail")


def test_complete_range_accepts_explicit_utc_offsets():
    season = _season_with_range(
        "2026-05-06T02:00:00+08:00",
        "2026-08-05T02:00:00+08:00",
    )

    start, end = apex_service._require_complete_season_range(season)

    assert start == datetime(2026, 5, 5, 18, tzinfo=timezone.utc)
    assert end == datetime(2026, 8, 4, 18, tzinfo=timezone.utc)


def test_update_current_split_state_accepts_injected_now():
    season = _season_with_range(
        "2026-05-05T18:00:00Z",
        "2026-08-04T18:00:00Z",
    )
    apex_service._apply_ranked_split_details(season, {})

    apex_service._update_current_split_state(
        season,
        now=datetime(2026, 6, 10, tzinfo=timezone.utc),
    )

    assert season.current_split_label == "上半赛季"
    assert season.current_split_index == 1
    assert season.next_transition_label == "下半赛季开始"

    apex_service._update_current_split_state(
        season,
        now=datetime(2026, 6, 23, 17, tzinfo=timezone.utc),
    )

    assert season.current_split_label == "下半赛季"
    assert season.current_split_index == 2
    assert season.next_transition_label == "赛季结束"


def test_resolve_season_status_accepts_injected_now():
    start_iso = "2026-05-05T18:00:00Z"
    end_iso = "2026-08-04T18:00:00Z"

    assert apex_service._resolve_season_status(
        start_iso,
        end_iso,
        now=datetime(2026, 5, 5, 17, tzinfo=timezone.utc),
    ) == "未开始"
    assert apex_service._resolve_season_status(
        start_iso,
        end_iso,
        now=datetime(2026, 7, 10, tzinfo=timezone.utc),
    ) == "进行中"
    assert apex_service._resolve_season_status(
        start_iso,
        end_iso,
        now=datetime(2026, 8, 4, 18, tzinfo=timezone.utc),
    ) == "已结束"


def test_cached_season_refreshes_derived_state_on_each_hit():
    season = _season_with_range(
        "2026-05-05T18:00:00Z",
        "2026-08-04T18:00:00Z",
    )
    apex_service._apply_ranked_split_details(season, {})
    client = _client_with_pages({})
    client._season_cache["season:current"] = (
        apex_service.time.monotonic(),
        season,
    )

    before = client._get_cached_season(
        "season:current",
        now=datetime(2026, 6, 10, tzinfo=timezone.utc),
    )

    assert before is season
    assert before.status_text == "进行中"
    assert before.current_split_label == "上半赛季"
    assert before.current_split_index == 1
    assert before.next_transition_label == "下半赛季开始"

    after = client._get_cached_season(
        "season:current",
        now=datetime(2026, 6, 23, 17, tzinfo=timezone.utc),
    )

    assert after is season
    assert after.status_text == "进行中"
    assert after.current_split_label == "下半赛季"
    assert after.current_split_index == 2
    assert after.next_transition_label == "赛季结束"
