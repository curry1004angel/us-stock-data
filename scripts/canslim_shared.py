# 두 데이터 레포가 바이트 단위로 동일하게 유지하는 판정 모듈의 드리프트를 검사하는 모듈
import hashlib
import json
from pathlib import Path

# 이 네 파일은 allstock과 us-stock-data에서 내용이 같아야 한다. 판정 로직이 두 갈래로
# 갈라지면 한쪽에서만 결함을 고치고 다른 쪽을 잊게 된다. canslim_loaders.py와
# canslim.py는 데이터 경로·지수 코드가 달라 의도적으로 레포별로 다르다.
SHARED_FILES = (
    "canslim_scoring.py", "canslim_bases.py",
    "canslim_items.py", "canslim_market.py",
)


def file_digest(path):
    """파일 내용의 SHA256 16진 문자열. 줄바꿈 차이도 잡아내도록 바이트로 읽는다."""
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def load_manifest(path):
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return data["files"]


def check_drift(scripts_dir, manifest_path):
    """공유 파일이 매니페스트와 어긋난 지점을 사람이 읽을 수 있는 문자열로 모아 돌려준다."""
    scripts_dir = Path(scripts_dir)
    expected = load_manifest(manifest_path)
    problems = []
    for name in SHARED_FILES:
        if name not in expected:
            problems.append(f"{name}: 매니페스트에 항목이 없다.")
            continue
        p = scripts_dir / name
        if not p.exists():
            problems.append(f"{name}: 파일이 없다 ({p}).")
            continue
        actual = file_digest(p)
        if actual != expected[name]:
            problems.append(
                f"{name}: 내용이 매니페스트와 다르다. "
                f"기대 {expected[name][:12]}..., 실제 {actual[:12]}...")
    return problems
