# XUI 1.5.5 com otimizações e Mass Edit

Instalador para servidor dedicado novo com Ubuntu 20.04, 22.04 ou 24.04 x86_64 e systemd. Use a licença oficial do XUI para ativar o painel. Os limites dos serviços são calculados com a RAM detectada. A release inclui o banco e o runtime da versão 1.5.5.

## Instalação nova

Execute no servidor de destino. O APT atualiza o índice de pacotes e o instalador instala as dependências e o XUI.

```bash
sudo apt update &&
curl -fL https://raw.githubusercontent.com/SavimaleDEV/xui-one-1.5.5/v1.5.5-update-20261122-r4/install.sh -o install-xui-1.5.5.sh &&
sudo bash install-xui-1.5.5.sh
```

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

Cinco testes automatizados passaram com o pacote 1.5.5. A aplicação em diretório isolado preservou o controlador PHP e as credenciais e uma segunda execução produziu zero alterações. O boot completo em VM nova, a ativação e o despacho de um lote com canais reais seguem sem validação.
