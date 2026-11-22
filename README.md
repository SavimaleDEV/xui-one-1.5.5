# XUI 1.5.5 com otimizações e Mass Edit

Instalador para servidor dedicado novo com Ubuntu 20.04, 22.04 ou 24.04 x86_64 e systemd. Use a licença oficial do XUI para ativar o painel. Os limites dos serviços são calculados com a RAM detectada. A release inclui o banco e o runtime da versão 1.5.5.

## Instalação nova

Execute no servidor de destino. O APT atualiza o índice de pacotes e o instalador instala as dependências e o XUI.

```bash
sudo apt update &&
curl -fL https://raw.githubusercontent.com/SavimaleDEV/xui-one-1.5.5/v1.5.5-update-20261122-r5/install.sh -o install-xui-1.5.5.sh &&
sudo bash install-xui-1.5.5.sh
```

## Zerar o XUI e reinstalar

Em servidor dedicado, use o script desta release com `sudo bash install-xui-1.5.5.sh --zerar-xui`. O painel sai do ar. O instalador recria os bancos locais `xui` e `xui_migrate`, as credenciais e a fila. Clientes, canais, configurações, licença e mídias antigas saem da instalação ativa. O dump dos bancos existentes e a lista dos arquivos movidos ficam em `/var/backups/xui-reset-*/`. As mídias locais antigas ficam arquivadas no mesmo disco. Os dois tmpfs de execução são desmontados. O instalador interrompe a reinstalação se encontrar outros discos montados na árvore do painel.

## Aplicar correções em uma instalação 1.5.5 existente

Faça backup do banco e da configuração. Conclua os lotes em andamento e reserve uma janela de manutenção. Com o script baixado acima, execute:

```bash
sudo systemctl stop xuione.service &&
sudo bash install-xui-1.5.5.sh --apply-updates &&
sudo systemctl start xuione.service
```

A atualização preserva banco, credenciais e licença. Os backups dos arquivos de configuração alterados ficam em `/var/backups/xui-updates/`.

## Alterações e validação

Inclui dimensionamento por RAM e CPU para PHP-FPM, MariaDB e Redis, limites systemd, proteção de abertura de canais no Nginx, rotação de logs, verificações de saúde e watchdog do MariaDB. Na edição em massa, a fila persiste entre reinícios. Mantenha a página aberta durante o despacho dos lotes.

Sete testes automatizados passaram com o pacote 1.5.5. A aplicação em diretório isolado preservou o controlador PHP e as credenciais e uma segunda execução produziu zero alterações. A limpeza dos dois bancos XUI e a restauração do dump foram verificadas em MariaDB local em contêiner isolado. O boot completo em VM nova, a ativação e o despacho de um lote com canais reais seguem sem validação.
