"""Wenshi index parsing: series/issue context and printed page numbers (review issues P-1, P-2)."""

from cah import extract, wenshi


def entries(html, ctx=None):
    return wenshi.parse_articles(html, {} if ctx is None else ctx)


def li(title, author, page):
    return f'<li><span class="t">{title}</span><span class="au">{author}</span><span class="pg">{page}</span></li>'


def test_imprint_lines_never_become_the_series():
    html = ('<h3>贵阳文史资料选辑</h3><h4>第 5 辑</h4><ul class="toc">' + li("回忆贵州地下党", "秦天真", "1") + "</ul>"
            "<p>出　　版：中国文史出版社</p><p>编　辑：山东省潍坊市政协文史资料委员会</p>"
            '<ul class="toc">' + li("贵州陆军史述要", "周素园", "51") + "</ul>")
    out = entries(html)
    assert [a["series"] for a in out] == ["贵阳文史资料选辑", "贵阳文史资料选辑"]
    assert [a["issue"] for a in out] == [5, 5]


def test_imprint_heading_from_the_model_is_ignored():
    ctx = {}
    entries("<h3>六盘水市文史资料选辑</h3><h4>第 2 辑</h4>", ctx)
    out = entries("<h3>出版：内部资料</h3>" + '<ul class="toc">' + li("六枝矿区", "陈连枭", "10") + "</ul>", ctx)
    assert out[0]["series"] == "六盘水市文史资料选辑" and out[0]["issue"] == 2


def test_context_carries_across_pages_without_corruption():
    ctx = {}
    entries('<h3>成都市文史资料选辑</h3><h4>第 1 辑</h4><p>编　辑：成都市政协文史委员会</p>', ctx)
    assert ctx == {"series": "成都市文史资料选辑", "issue": 1}


def test_fallback_lines_are_read_but_do_not_change_series():
    out = entries("<h3>上海文史资料选辑</h3><h4>第 3 辑</h4><p>王起华的回忆……………王起华(35)</p>"
                  "<p>上海文史资料存稿汇编</p>")
    assert out == [{"series": "上海文史资料选辑", "issue": 3, "title": "王起华的回忆", "author": "王起华", "page": 35}]


def test_rule_based_series_head_rejects_imprints():
    assert extract.is_series_head("六盘水市文史资料选辑")
    for line in ("出　　版：中国文史出版社", "编　辑：海宁市政协文史委员会", "主办：政协文史委", "文史资料：第一辑"):
        assert not extract.is_series_head(line), line


def test_page_ranges_keep_the_first_page_and_the_raw_value():
    out = entries('<ul class="toc">' + li("甲", "", "116—120") + li("乙", "", "期刊 6/95") + li("丙", "", "(42)")
                  + li("丁", "", "") + "</ul>")
    assert [(a["page"], a["page_raw"]) for a in out] == [(116, "116—120"), (None, "期刊 6/95"), (42, None), (None, None)]


def test_page_value_shapes():
    cases = {"168": (168, None), "（5）": (5, None), "( )": (None, None), "1 首 13": (13, "1 首 13"),
             "1首（164）": (164, "1首（164）"), "1首": (None, "1首"), "选辑13": (None, "选辑13"),
             "公元1888年": (None, "公元1888年"), "岳守荣(1)": (1, "岳守荣(1)"), "刘士裕302": (302, "刘士裕302"),
             "01-02": (1, "01-02"), "3.3–12": (None, "3.3–12"), "政治风云/事件": (None, "政治风云/事件"),
             "2688": (2688, None), "1951319533": (None, "1951319533")}
    assert {k: wenshi.page_value(k) for k in cases} == cases


def test_plain_page_number_has_no_raw_value():
    out = entries('<ul class="toc">' + li("甲", "某", "168") + "</ul>")
    assert out[0]["page"] == 168 and out[0]["page_raw"] is None
