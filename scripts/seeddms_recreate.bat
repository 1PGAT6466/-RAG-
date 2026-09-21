@echo off
REM ============================================================
REM SeedDMS 容器重建脚本（使用固化预览功能的镜像 seeddms-fuxi）
REM
REM 用途：把运行中的 seeddms 容器切换为带 LibreOffice + unoconv
REM      预览能力的镜像。原有数据/配置通过 bind mount 保留，不丢失。
REM
REM 何时运行：
REM   - 首次固化镜像后
REM   - 需要重新创建容器时（如改了端口/挂载）
REM
REM 重要：SeedDMS 的实际生效配置是 bind mount 的宿主机文件：
REM       E:\测试项目\SeedDMS\conf\settings.xml
REM       （容器内路径 /var/lib/seeddms/conf/settings.xml，由
REM        env SEEDDMS_CONFIG_FILE 指定；/home/www-data/seeddms60x/conf/
REM        下的同名文件不是生效配置，别改错）
REM ============================================================

set IMAGE=seeddms-fuxi:latest
set NAME=seeddms

echo [1/4] 停止并删除旧容器...
docker stop %NAME%
docker rm %NAME%

echo [2/4] 用固化镜像重建容器...
docker run -d ^
  --name %NAME% ^
  --restart unless-stopped ^
  -p 8080:80 ^
  -v "E:\测试项目\SeedDMS\data:/var/lib/seeddms/data" ^
  -v "E:\测试项目\SeedDMS\conf:/var/lib/seeddms/conf" ^
  %IMAGE%

echo [3/4] 等待容器启动...
timeout /t 8 /nobreak >nul

echo [4/4] 验证预览能力...
docker exec %NAME% sh -c "which unoconv && libreoffice --version | head -1"
docker exec %NAME% python3 -c "c=open('/var/lib/seeddms/conf/settings.xml').read(); print('convertToPdf on:', 'convertToPdf=\"true\"' in c); print('pdfviewer on:', 'pdfviewer\" disable=\"false\"' in c)"

echo.
echo 完成。SeedDMS 运行在 http://localhost:8080
pause
