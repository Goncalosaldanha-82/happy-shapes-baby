# Baby Sensory: um vídeo sensorial original por dia para bebés (0–3 anos)

## Porquê este formato
- Os canais infantis estão entre os mais vistos do YouTube (canções infantis, vídeos sensoriais e de aprendizagem). Os vídeos sensoriais para bebés (formas de alto contraste a dançar com música) são vistos sobretudo em vídeos longos que os pais põem na TV, e continuam a ter procura durante anos.
- Sem palavras: só imagem e música, por isso funciona em qualquer país.
- Tudo é gerado por código, tal como no Ball League: formas desenhadas com cairo e música sintetizada com numpy (caixa de música, marimba, kalimba e pad). Não há IA generativa, samples nem personagens de outros canais, por isso não há direitos de terceiros.

## O que cada episódio tem
- Um vídeo longo de 1920×1080 com cerca de 14 minutos (o YouTube rejeita vídeos com mais de 15 min enquanto o canal não for verificado por telemóvel; depois da verificação pode usar-se `--minutes 30`), feito de cenas de cerca de 38 s que mudam com um fade suave.
- Um Short de 1080×1920 com cerca de 40 s (a primeira cena), com ligação para o vídeo longo.
- Uma miniatura de 1280×720, sem texto.
- O tema roda todos os dias: Dance Party, Bubble Party, Black & White High Contrast, Shape Parade, Rainbow Kaleidoscope, Spinning Friends e Starry Night Calm Lullaby (este último mais lento e sem batida, para adormecer).

## Seguro para bebés
- Não há flashes nem estroboscópio. As mudanças de cena fazem fade para preto em 0,6 s e os movimentos são lentos.
- O volume é suave (média de cerca de −19 dBFS e limitador) e não há sons súbitos.
- Todos os vídeos vão marcados como **"made for kids"**, o que é obrigatório por lei (COPPA). Consequências: não há comentários nem anúncios personalizados e o RPM é mais baixo.

## Comandos
```
python make_video.py              # próximo episódio -> output/ep_NNN/ (video.mp4, short.mp4, thumbnail.png, metadata.json)
python make_video.py --preview    # teste rápido em baixa resolução
python make_video.py --minutes 30 # episódio mais longo
python upload.py --auth           # login único: escolhe o canal de bebés e confirma
python upload.py --dry-run        # mostra o que seria publicado
```
Um render de 20 min demora cerca de 30 min.

## Configurar o canal (uma vez)
1. No YouTube, com a tua conta Google, vai a **Definições → Adicionar ou gerir canais → Criar um canal**. Isto cria uma conta de marca separada, como fizeste para o Ball League e o Golden Tides.
2. Verifica o canal por telemóvel em youtube.com/verify. Sem isto, as miniaturas personalizadas não funcionam.
3. Corre `python upload.py --auth`, entra, escolhe o **novo canal** e confirma com `y`. O id fica guardado em `config.json` e o token em `token_baby.json`.
   O cliente OAuth é o do Ball League (`../ball-league/client_secret.json`), que já está "In production".
4. Quota: cada episódio gasta cerca de 3 250 unidades (2 vídeos + miniatura). Somado ao Ball League e ao canal de música, fica abaixo das 10 000 por dia.

## Publicação diária às 06:00 (GitHub Actions)
Tudo corre nos servidores do GitHub (workflow `.github/workflows/daily.yml`). Não depende do PC estar ligado e não ocupa disco.

- O workflow corre às 10:30, 14:30 e 20:30 UTC, depois de a quota diária da API se renovar (~08:00 UTC). Cada execução corre `daily.py`, que gera o próximo episódio e o envia para o YouTube como privado com publicação marcada (`publishAt`) para as **06:00 de Lisboa** do primeiro dia livre. Quem publica é o próprio YouTube, por isso a hora é exata mesmo que o GitHub corra atrasado. O Short sai à mesma hora.
- Mantém até 7 dias agendados. As execuções extra servem para encher essa reserva ou repetir o que falhou. Nunca há dois episódios no mesmo dia.
- O registo do que está agendado são os ficheiros `output/ep_NNN/metadata.json` e `uploaded.json`, que o workflow guarda no repositório. Os vídeos não ficam guardados: estão no YouTube.
- Cada episódio sai sempre igual a partir do seu número. Se um render ou envio falhar (por exemplo, por falta de quota), o mesmo episódio é gerado outra vez na execução seguinte.
- O token do canal é o secret encriptado `BABY_TOKEN_JSON`. Se a autorização expirar, corre `python upload.py --auth` localmente e atualiza o secret com:
  `gh secret set BABY_TOKEN_JSON -R Goncalosaldanha-82/happy-shapes-baby < token_baby.json`
- Quota: cada episódio gasta cerca de 3 250 unidades das 10 000 diárias, partilhadas com o Ball League e o Golden Tides. Se acabar, o log diz `QUOTA EXCEEDED` e o resto segue na execução seguinte. Isto não é um erro.
- Se o YouTube rejeitar um vídeo, o log diz `LONG VIDEO REJECTED`.
- Para gerar à mão: em Actions → "Happy Shapes Baby daily" → **Run workflow**, onde se pode pedir mais episódios.

A tarefa local `BabySensory` está desativada, para não haver envios duplicados. `run_daily.bat` continua a funcionar como alternativa manual.
