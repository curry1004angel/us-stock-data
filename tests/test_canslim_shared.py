# 두 데이터 레포가 공유하는 판정 모듈이 갈라지지 않았는지 검사하는 테스트
import hashlib
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import canslim_shared as sh

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
MANIFEST = SCRIPTS / "canslim_shared_manifest.json"


def test_공유_파일_목록이_네_개다():
    assert sh.SHARED_FILES == (
        "canslim_scoring.py", "canslim_bases.py",
        "canslim_items.py", "canslim_market.py",
    )


def test_해시는_파일_내용이_바뀌면_바뀐다(tmp_path):
    p = tmp_path / "a.py"
    p.write_text("x = 1\n", encoding="utf-8")
    first = sh.file_digest(p)
    p.write_text("x = 2\n", encoding="utf-8")
    assert sh.file_digest(p) != first


def test_해시가_표준_sha256과_같다(tmp_path):
    p = tmp_path / "a.py"
    p.write_bytes(b"hello")
    assert sh.file_digest(p) == hashlib.sha256(b"hello").hexdigest()


def test_매니페스트에_네_파일이_모두_있다():
    m = sh.load_manifest(MANIFEST)
    assert set(m) == set(sh.SHARED_FILES)


def test_공유_파일이_매니페스트와_일치한다():
    # 이 테스트가 실패하는 원인은 셋이다. (1) 공유 판정 파일이 다른 레포와 갈라졌다.
    # (2) 의도적으로 고친 뒤 매니페스트를 갱신하지 않았다. (3) 체크아웃 시 줄바꿈이
    # 바뀌었다. file_digest는 워킹트리 바이트를 그대로 해시하므로 CRLF와 LF가 섞이면
    # 내용이 같아도 해시가 달라진다. 네 파일이 한꺼번에 다 어긋난다면 (1)이나 (2)가
    # 아니라 (3)을 먼저 의심한다. 내용 드리프트가 네 파일에서 동시에 일어날 가능성은
    # 낮지만, 줄바꿈 환경이 바뀌면 네 파일 모두가 한 번에 영향을 받기 때문이다.
    # 어느 원인이든 두 레포를 같이 봐야 한다.
    problems = sh.check_drift(SCRIPTS, MANIFEST)
    assert problems == [], "\n".join(problems)


def test_매니페스트에_항목이_없으면_문제로_보고한다(tmp_path):
    (tmp_path / "canslim_scoring.py").write_text("x=1\n", encoding="utf-8")
    manifest = tmp_path / "m.json"
    manifest.write_text(
        '{"files": {"canslim_scoring.py": "%s"}}' % sh.file_digest(tmp_path / "canslim_scoring.py"),
        encoding="utf-8",
    )
    problems = sh.check_drift(tmp_path, manifest)
    matches = [p for p in problems if "canslim_bases.py" in p]
    assert matches, problems
    assert "매니페스트에 항목이 없다" in matches[0]
    assert "파일이 없다" not in matches[0]


def test_디스크에_파일이_없으면_파일이_없다로_보고한다(tmp_path):
    scoring = tmp_path / "canslim_scoring.py"
    scoring.write_text("x=1\n", encoding="utf-8")
    manifest = tmp_path / "m.json"
    manifest.write_text(
        '{"files": {'
        '"canslim_scoring.py": "%s", '
        '"canslim_bases.py": "%s", '
        '"canslim_items.py": "%s", '
        '"canslim_market.py": "%s"'
        '}}' % (sh.file_digest(scoring), "0" * 64, "0" * 64, "0" * 64),
        encoding="utf-8",
    )
    problems = sh.check_drift(tmp_path, manifest)
    matches = [p for p in problems if "canslim_bases.py" in p]
    assert matches, problems
    assert "파일이 없다" in matches[0]
    assert "매니페스트에 항목이 없다" not in matches[0]
