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
    # 이 테스트가 실패하면 공유 판정 파일이 다른 레포와 갈라졌거나, 의도적으로 고친 뒤
    # 매니페스트를 갱신하지 않은 것이다. 어느 쪽이든 두 레포를 같이 봐야 한다.
    problems = sh.check_drift(SCRIPTS, MANIFEST)
    assert problems == [], "\n".join(problems)


def test_파일이_없으면_문제로_보고한다(tmp_path):
    (tmp_path / "canslim_scoring.py").write_text("x=1\n", encoding="utf-8")
    manifest = tmp_path / "m.json"
    manifest.write_text(
        '{"files": {"canslim_scoring.py": "%s"}}' % sh.file_digest(tmp_path / "canslim_scoring.py"),
        encoding="utf-8",
    )
    problems = sh.check_drift(tmp_path, manifest)
    assert any("canslim_bases.py" in p for p in problems)
