"""Prepare the tested release for the main hostname and its verified TLS route."""
from pathlib import Path
import hashlib
import io
import json
import re
import tarfile

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'tmp/main-release'
copy = {
'en': 'The site is delivered through Cloudflare on hosting provided by Hetzner. The main site inkcoverage.app and the separate beta.inkcoverage.app use HTTPS from your browser to Cloudflare and certificate-validated HTTPS from Cloudflare to the origin. The main-site configuration was verified on 16 October 2026. A local development preview uses loopback HTTP. Cloudflare handles traffic passing through its service and may apply security cookies according to its settings.',
'es': 'El sitio se entrega mediante Cloudflare y utiliza alojamiento de Hetzner. El sitio principal inkcoverage.app y el sitio independiente beta.inkcoverage.app utilizan HTTPS entre el navegador y Cloudflare, y HTTPS con validación del certificado entre Cloudflare y el origen. La configuración del sitio principal se verificó el 16 de octubre de 2026. La vista previa local utiliza HTTP de bucle local. Cloudflare procesa el tráfico que pasa por su servicio y puede aplicar cookies de seguridad según sus ajustes.',
'pt': 'O site é distribuído pelo Cloudflare e hospedado na Hetzner. O site principal inkcoverage.app e o site separado beta.inkcoverage.app usam HTTPS do navegador ao Cloudflare e HTTPS com validação do certificado do Cloudflare à origem. A configuração do site principal foi verificada em 16 de outubro de 2026. A prévia local usa HTTP de loopback. O Cloudflare processa o tráfego que passa pelo serviço e pode aplicar cookies de segurança conforme suas configurações.',
'zh': '网站通过 Cloudflare 分发，由 Hetzner 托管。主站 inkcoverage.app 和独立测试站 beta.inkcoverage.app 在浏览器与 Cloudflare 之间使用 HTTPS，在 Cloudflare 与源站之间使用经过证书验证的 HTTPS。主站配置已于 2026 年 10 月 16 日验证。本地开发预览使用回环 HTTP。Cloudflare 处理经过其服务的流量，并可能根据设置使用安全 Cookie。',
'de': 'Die Website wird über Cloudflare auf Hetzner-Hosting bereitgestellt. Die Hauptseite inkcoverage.app und die separate beta.inkcoverage.app nutzen HTTPS vom Browser zu Cloudflare und zertifikatsgeprüftes HTTPS von Cloudflare zum Ursprung. Die Konfiguration der Hauptseite wurde am 16. Oktober 2026 geprüft. Eine lokale Entwicklungsvorschau verwendet Loopback-HTTP. Cloudflare verarbeitet den über seinen Dienst laufenden Verkehr und kann je nach Einstellungen Sicherheits-Cookies setzen.',
'fr': 'Le site est distribué par Cloudflare et hébergé chez Hetzner. Le site principal inkcoverage.app et le site distinct beta.inkcoverage.app utilisent HTTPS du navigateur à Cloudflare, puis HTTPS avec validation du certificat jusqu’à l’origine. La configuration du site principal a été vérifiée le 16 octobre 2026. L’aperçu de développement local utilise HTTP en boucle locale. Cloudflare traite le trafic passant par son service et peut appliquer des cookies de sécurité selon ses réglages.',
'uk': 'Сайт подається через Cloudflare на хостингу Hetzner. Основний сайт inkcoverage.app і окремий сайт beta.inkcoverage.app використовують HTTPS від браузера до Cloudflare та HTTPS із перевіркою сертифіката від Cloudflare до сервера. Конфігурацію основного сайту перевірено 16 жовтня 2026 року. Локальний перегляд розробки використовує HTTP через петльову адресу. Cloudflare обробляє трафік свого сервісу й може застосовувати захисні cookie відповідно до налаштувань.'
}
dates = dict(en='Last updated: October 16, 2026', es='Última actualización: 16 de octubre de 2026', pt='Última atualização: 16 de outubro de 2026', zh='最后更新：2026 年 10 月 16 日', de='Zuletzt aktualisiert: 16. Oktober 2026', fr='Dernière mise à jour : 16 octobre 2026', uk='Останнє оновлення: 16 жовтня 2026 року')
for lang, value in copy.items():
    path = ROOT / ('static/privacy' + ('' if lang == 'en' else '-' + lang) + '.html')
    source = path.read_text(encoding='utf-8')
    count = [0]
    def replace(match):
        count[0] += 1
        return match[1] + (value if count[0] == 5 else match[2]) + match[3]
    source = re.sub(r'(<h3>.*?</h3><p>)(.*?)(</p>)', replace, source, flags=re.S)
    assert count[0] == 8, (lang, count)
    source = re.sub(r'(<p class="last-updated">).*?(</p>)', lambda m: m[1] + dates[lang] + m[2], source)
    path.write_text(source, encoding='utf-8')
    if lang in ('de','fr','uk'):
        path = ROOT / f'scripts/languages/pages-{lang}.json'
        c = json.loads(path.read_text(encoding='utf-8')); c['privacy'][4][1] = value; c['updated'] = dates[lang]
        path.write_text(json.dumps(c, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
compose = (ROOT / 'deploy/compose-beta.yml').read_text(encoding='utf-8')
compose = compose.replace('name: inkcoverage-beta', 'name: inkcoverage-main').replace('inkcoverage-beta:seven-languages-20261016', 'inkcoverage-main:2.0-20261016').replace('127.0.0.1:8003:8000','127.0.0.1:8004:8000').replace('beta-files','main-files')
(ROOT / 'deploy/compose-main.yml').write_text(compose, encoding='utf-8')
previous = json.loads((ROOT / 'tmp/seven-languages/release-manifest.json').read_text(encoding='utf-8'))
manifest = {}
with tarfile.open(OUT / 'main-2.0-20261016.tar.gz','w:gz') as archive:
    for name in sorted(previous):
        path = ROOT / ('deploy/compose-main.yml' if name == 'compose.yml' else name)
        data = path.read_bytes()
        if path.suffix in ('.py','.html','.css','.js','.json','.yml','.txt','.xml') or name in ('Dockerfile','.dockerignore'): data = data.replace(b'\r\n',b'\n')
        manifest[name] = hashlib.sha256(data).hexdigest()
        info = tarfile.TarInfo(name); info.size = len(data); info.mode = 0o644
        archive.addfile(info,io.BytesIO(data))
(OUT / 'main-manifest.json').write_text(json.dumps(manifest,indent=2),encoding='utf-8')
digest = hashlib.sha256((OUT / 'main-2.0-20261016.tar.gz').read_bytes()).hexdigest()
(OUT / 'archive.sha256').write_text(digest + '  main-2.0-20261016.tar.gz\n',encoding='ascii')
print('Main release:',len(manifest),'files; SHA-256',digest)
