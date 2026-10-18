# CD Shelf (CD 보관함)

CD를 드라이브에 넣으면 MusicBrainz에서 앨범·트랙리스트·발매정보를, Cover Art Archive에서 커버를 가져와
저장합니다. 트랙별 별점, 앨범 평균 자동 계산, 전체 검색을 지원하는 Windows 데스크톱 앱입니다.

## 설치 (사용자)
1. [Releases](../../releases/latest)에서 `CDShelf-Setup.exe` 다운로드 후 실행
2. 설치 중 "Create a desktop icon" 체크 → 바탕화면 아이콘 생성
3. 작업 표시줄 고정: 앱 실행 후 작업 표시줄 아이콘을 우클릭 → "작업 표시줄에 고정"
   (Windows 정책상 설치 프로그램이 대신 고정할 수 없습니다)
4. 폰에서 보기: 앱 첫 화면의 `http://PC주소:5000`을 같은 와이파이의 폰 브라우저에 입력
   (처음 실행 시 방화벽 허용 창에서 "개인 네트워크" 허용)

데이터는 `%APPDATA%\CDShelf`(DB `cdshelf.db`, 커버 `covers\`)에 저장되며 업데이트해도 유지됩니다.

## 자동 업데이트
- 앱을 켤 때마다 GitHub 최신 릴리스를 확인하고, 화면 아래 `업데이트 확인` 버튼으로 수동 확인도 가능합니다.
- 새 버전이 있으면 "지금 업데이트" → 설치 파일 다운로드(해시 검증) → 자동 설치 → 앱 재실행.

## 새 버전 배포 (개발자)
```
git add -A && git commit -m "변경 내용"
git tag v1.0.1
git push origin main --tags
```
태그(`v*`)를 푸시하면 GitHub Actions가 EXE 빌드 → 설치 프로그램 생성 → Release 업로드까지 자동 수행합니다.
태그 번호가 기존 설치본보다 커야 업데이트로 인식됩니다.

## 로컬 실행 (개발)
```
pip install -r requirements.txt
python main.py
```
CD 읽기는 Windows에서 `discid.dll`이 필요합니다(`vendor/discid.dll` 또는 PATH).

## 한계
- 조회되지 않는 CD는 수동 입력 화면 사용
- 설치 파일에 코드 서명이 없어 Windows SmartScreen 경고가 뜰 수 있습니다("추가 정보 → 실행")
