# Outras fontes de b-roll que podemos integrar

> Termos de uso, limites e preços mudam com frequência. Confirme na página oficial de cada fonte antes de
> integrar ou publicar. O que está abaixo é um ponto de partida, não uma consultoria jurídica.

## Fontes atuais

| Fonte | API | Licença (resumo) | Limites | Observações |
|---|---|---|---|---|
| **Pexels** | Sim, gratuita com chave | Uso gratuito, inclusive comercial; atribuição não obrigatória, mas apreciada | 200 req/h e 20.000/mês por padrão (dá para pedir aumento) | As diretrizes da API pedem link visível para o Pexels e crédito ao autor quando possível. Não vender o conteúdo sem alteração. |
| **Pixabay** | Sim, gratuita com chave | Content License: uso gratuito, sem atribuição | 100 req a cada 60s | A API **exige cache de 24h** e proíbe hotlink permanente (é preciso baixar). Mostrar que os resultados vêm da Pixabay. |
| **Mixkit** (local) | Não tem API pública | Mixkit License: gratuito, inclusive comercial, sem atribuição | — | Baixar manualmente; scraping não é permitido. Não redistribuir os clipes como arquivos avulsos. |

## Candidatas gratuitas

| Fonte | API | Tipo de licença | Limites | Qualidade do acervo | Prós | Contras |
|---|---|---|---|---|---|---|
| **Coverr** | Sim (chave gratuita mediante cadastro) | Licença própria: gratuita, comercial, sem atribuição | Limites por chave (ver painel de dev) | Boa, estilo lifestyle/tech, acervo pequeno | API simples, licença clara, bons clipes horizontais | Acervo menor; muita repetição de temas |
| **Videvo** | Sem API pública aberta (parcerias) | **Varia por clipe**: licença Videvo, Creative Commons BY 3.0 ou premium | — | Média a boa | Acervo grande e variado | Licença mista exige checar item a item; sem API, só uso manual/local |
| **Mazwai** | Não | CC BY 3.0 e licença Mazwai (atribuição em parte do acervo) | — | Boa, cinematográfica | Visual "de filme", pouco batido | Acervo pequeno; atribuição; sem API (só biblioteca local) |
| **Openverse** | Sim (anônimo com throttling; app registrado tem limite maior) | Creative Commons variadas e domínio público | Limites por minuto/dia | Variável (imagens e áudio, **sem vídeo**) | Agrega milhões de imagens; filtro por licença na API (`license_type=commercial,modification`) | Só imagens; qualidade irregular; muitas exigem atribuição |
| **Wikimedia Commons** | Sim (MediaWiki API, sem chave; exige User-Agent) | CC BY / CC BY-SA / domínio público | Uso "educado" (sem limite fixo rígido) | Variável; ótimo para lugares, história, ciência | Conteúdo factual e documental difícil de achar em stock | Muitas licenças BY-SA (atribuição + compartilhar igual); vídeos em WebM; baixa resolução frequente |
| **Internet Archive** | Sim (Advanced Search / Scrape API) | Por item: domínio público (ex.: Prelinger Archives), CC ou sem licença clara | Sem limite oficial rígido; downloads lentos | Vintage/arquivo; baixa resolução | Imagens de arquivo e históricas, únicas | Licença precisa ser checada por item; qualidade antiga |
| **NASA Image and Video Library** | Sim (`images-api.nasa.gov`, sem chave) | Em geral domínio público | Generoso | Excelente para espaço, ciência e Terra | Material único e oficial, em alta resolução | Nicho; proibido usar insígnias/logos da NASA ou sugerir endosso; parte do material é de terceiros |
| **Unsplash** (imagens) | Sim (Demo: 50 req/h; Produção: 5.000/h após aprovação) | Unsplash License: gratuita, sem atribuição obrigatória | Por hora | Excelente em fotografia | Fotos de altíssima qualidade para Ken Burns | As diretrizes da API pedem hotlink, disparar o endpoint de *download* e creditar autor + Unsplash; avaliar se o uso em vídeo renderizado está de acordo |

## Candidatas pagas

| Fonte | API | Licença | Limites / modelo | Qualidade | Prós | Contras |
|---|---|---|---|---|---|---|
| **Storyblocks** | Sim (API para parceiros/empresas) | Royalty-free por assinatura | Assinatura com downloads ilimitados | Boa e muito ampla | Custo previsível para alto volume; API feita para integração | Contrato de API é comercial (negociado); qualidade menos "premium" que Artgrid |
| **Shutterstock** | Sim (API pública; licenciamento via API) | Royalty-free (standard/enhanced) | Por plano/crédito | Muito ampla e boa | Maior acervo; busca e licença 100% automatizáveis; previews com marca d'água para ranquear antes de pagar | Caro por clipe HD/4K; precisa lidar com o fluxo de licença |
| **Adobe Stock** | Sim (API para parceiros) | Royalty-free por licença | Por plano/crédito | Muito boa | Acervo grande e bem curado; integração com o ecossistema Adobe | Acesso à API de licenciamento exige parceria; custo por item |
| **Envato Elements** | Sem API pública de conteúdo | Assinatura; cada download é registrado para um projeto | Downloads ilimitados na assinatura | Boa, muito variada | Excelente custo-benefício para uso manual (vídeo, música, SFX) | Sem API: só como biblioteca local, igual ao Mixkit; licença vinculada ao projeto registrado |
| **Artgrid** | Sem API pública | Assinatura (licença vitalícia para o que foi baixado na vigência) | Downloads ilimitados | Excelente, cinematográfica (inclusive RAW/LOG) | Visual premium, zero "cara de stock" | Sem API; arquivos pesados; só como biblioteca local |
| **Pond5** | API para parceiros | Royalty-free por clipe | Por item | Ampla, com muito material de arquivo/notícia | Material editorial e histórico | Preço por clipe; acesso à API sob acordo |
| **Getty Images / iStock** | Sim (API empresarial) | Royalty-free e editorial | Contrato | Topo de linha (editorial e criativo) | Único com cobertura editorial forte | Caro; contrato empresarial |

## Recomendação para este projeto

1. **Coverr** como 4ª fonte de API: gratuita, licença simples e o mesmo perfil de acervo de Pexels e Pixabay.
   A integração é direta com `BaseProvider`.
2. **NASA Image Library** como fonte temática, ativada quando o roteiro for de ciência, espaço ou clima:
   material excelente, sem chave e em domínio público.
3. **Openverse e Wikimedia Commons** só para **imagens** e com filtro de licença (apenas `cc0`, `pdm`,
   `by`). Os créditos já saem no `report.json`. Ajudam em cenas factuais (lugares, pessoas históricas,
   objetos específicos) onde os acervos de stock falham.
4. Para escalar com qualidade: **Storyblocks** (custo fixo, API) ou **Shutterstock** (maior acervo,
   licença via API; dá para ranquear os previews com CLIP e só licenciar o vencedor, o que encaixa
   perfeitamente no fluxo "baixar só os melhores").
5. **Envato Elements e Artgrid** como bibliotecas locais (mesmo mecanismo do Mixkit) para dar um visual
   mais premium sem depender de API.

## Pontos de atenção sobre licenças e termos

- **Atribuição:** Pexels, Pixabay, Mixkit, Coverr e Unsplash não exigem atribuição no vídeo. Parte do
  Videvo e do Mazwai e muitos itens de Openverse/Wikimedia exigem (CC BY / BY-SA). O `report.json` traz
  autor e URL de cada clipe usado, prontos para a descrição do vídeo.
- **CC BY-SA** ("compartilhar igual") pode obrigar a licenciar a obra derivada nos mesmos termos. Evite
  em vídeos comerciais ou isole em cenas não essenciais.
- **Proibição de redistribuição:** praticamente todas as fontes proíbem revender ou redistribuir o arquivo
  original sem alteração (ex.: subir o clipe cru em outro banco de imagens). Usar dentro de um vídeo editado
  é permitido.
- **Pessoas e marcas identificáveis:** a licença da mídia não cobre direito de imagem nem marca registrada
  em usos ofensivos, enganosos ou que sugiram endosso. Isso vale para Pexels e Pixabay e é explícito para
  logos da NASA.
- **Scraping:** Mixkit, Videvo, Mazwai, Envato e Artgrid não oferecem API e não permitem coleta
  automatizada. Use download manual e a biblioteca local. O bot não faz scraping de nenhum site.
- **Cache e hotlink:** a Pixabay exige cache de 24h e proíbe hotlink permanente. A Unsplash, ao contrário,
  pede hotlink e o disparo do endpoint de download. Respeite a regra de cada API.
- **Material editorial** (Getty, Pond5, Internet Archive): alguns itens são só para uso editorial ou
  jornalístico e não podem ir em publicidade.
