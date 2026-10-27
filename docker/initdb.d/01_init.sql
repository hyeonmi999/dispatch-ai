-- 최초 컨테이너 생성 시 1회 실행됩니다.
-- 테이블은 FastAPI 서버 시작 시 SQLAlchemy가 자동 생성합니다(create_all).
CREATE DATABASE IF NOT EXISTS dispatch_ai DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
GRANT ALL PRIVILEGES ON dispatch_ai.* TO 'manager'@'%';
FLUSH PRIVILEGES;
