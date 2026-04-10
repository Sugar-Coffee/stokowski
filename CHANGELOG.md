# Changelog

All notable changes to Stokowski are documented here.

---

## [0.6.0](https://github.com/erikpr1994/stokowski/compare/v0.5.0...v0.6.0) (2026-04-10)


### Features

* accessible UI, comprehensive tests for new features ([#73](https://github.com/erikpr1994/stokowski/issues/73)) ([ff351c3](https://github.com/erikpr1994/stokowski/commit/ff351c30aad333827ed3737b4292671c21735ac7))
* add /release slash command ([72f53f6](https://github.com/erikpr1994/stokowski/commit/72f53f6334f25576a918879d66e0501708b370ad))
* add Codex runner and multi-runner routing ([8ff0e74](https://github.com/erikpr1994/stokowski/commit/8ff0e7473b0ffdd6839abd4821f7199be340c891))
* add completed_at field to RunAttempt ([1fb5d73](https://github.com/erikpr1994/stokowski/commit/1fb5d731627d84cd72f28605ccbdb33f213c3a5b))
* add Gemini CLI as a supported runner ([3ef91f7](https://github.com/erikpr1994/stokowski/commit/3ef91f7b7e3f38748c516e3b9555d2cbffa7a64d))
* add Gemini CLI as a supported runner ([f65996e](https://github.com/erikpr1994/stokowski/commit/f65996e2ed23281e88fae40bb640514a6c8a14e3)), closes [#10](https://github.com/erikpr1994/stokowski/issues/10)
* add Linear comment, state mutation, and comment query methods ([e475351](https://github.com/erikpr1994/stokowski/commit/e475351f41f579e6e73cbbf3ea7ca99730fc8630))
* add on_stage_enter hook support for pipeline stages ([c5852c4](https://github.com/erikpr1994/stokowski/commit/c5852c4239b944376a0dd7356fce95d0f774d15a))
* add pipeline stage resolution, gate protocol, and rework handling ([b100531](https://github.com/erikpr1994/stokowski/commit/b1005319f26b373966c1e067af25344f0a50fa6c))
* add pipeline stage tracking via structured Linear comments ([1a684c4](https://github.com/erikpr1994/stokowski/commit/1a684c4ee4ae5e5d935681902541c665f22768a9))
* add pipeline validation - gates, rework targets, and state config ([a4dd34d](https://github.com/erikpr1994/stokowski/commit/a4dd34d6a2e109f8881e909cfa0e1809af4379e2))
* add pipeline, stage, and gate config dataclasses ([8b769d8](https://github.com/erikpr1994/stokowski/commit/8b769d891b70ee295eaf053c0115573c3359edf9))
* add specific workflow information for reworks ([157a3b9](https://github.com/erikpr1994/stokowski/commit/157a3b9b5b5d5e091d772a0685ed7f16ba19a288))
* add stage and runner_type fields to RunAttempt ([da63359](https://github.com/erikpr1994/stokowski/commit/da63359cb98276d5925b614b41a3863937e5bd70))
* add stall detection to Codex runner ([db58f04](https://github.com/erikpr1994/stokowski/commit/db58f047a2cd6fe948fa0363e8f89e53fef4bfa9))
* add three-layer prompt assembly for state machine workflows ([a2d61fd](https://github.com/erikpr1994/stokowski/commit/a2d61fd14fdde26ee094bc59e3ebb69c32370b9c))
* add todo state — pick up issues from Todo and move to In Progress ([94b9d02](https://github.com/erikpr1994/stokowski/commit/94b9d020698f0e97fc61c137996b4ed6b4c8e4fc))
* add worktree workspace mode, configurable headless prompt, and peakHealth workflows ([2e0c7d8](https://github.com/erikpr1994/stokowski/commit/2e0c7d8e682ded727877f1e716c81e2a5690e21e))
* auto-detect .stokowski/stokowski.yaml, read port from config ([2741e5c](https://github.com/erikpr1994/stokowski/commit/2741e5c5b58ea36897717082993f16280b0e5865))
* auto-detect config, read port — just run stokowski ([a4b1c8f](https://github.com/erikpr1994/stokowski/commit/a4b1c8fc0cbd006bff4043bd608ae5c8533650e0))
* auto-migrate shared fields from workflow to root config ([068133b](https://github.com/erikpr1994/stokowski/commit/068133b2d76a7fa369347f372e565bd7a87ad808))
* configure both Linear and GitHub webhooks in init ([#86](https://github.com/erikpr1994/stokowski/issues/86)) ([9d412f1](https://github.com/erikpr1994/stokowski/commit/9d412f17a04d2c1a0e39cbd07560b908501828b1))
* crash recovery — persist and restore per-issue state ([#80](https://github.com/erikpr1994/stokowski/issues/80)) ([2974b97](https://github.com/erikpr1994/stokowski/commit/2974b9754fc88cdfa08821db1a6a94a218de2bbc))
* dispatch queue + API cooldown on rate limit errors ([#96](https://github.com/erikpr1994/stokowski/issues/96)) ([1baec5f](https://github.com/erikpr1994/stokowski/commit/1baec5f4cd2752619f11196b9a3c7d5b45543139)), closes [#69](https://github.com/erikpr1994/stokowski/issues/69)
* exclude_labels filter + support for pr-lifecycle and learn workflows ([#82](https://github.com/erikpr1994/stokowski/issues/82)) ([2ae492b](https://github.com/erikpr1994/stokowski/commit/2ae492b3179a5cc1c828aaeb4b9e79bc8412566f))
* fallback runner chain on rate limit errors ([a87725c](https://github.com/erikpr1994/stokowski/commit/a87725cf392b4c19c524b56a24fc56d3ed10fe86))
* fallback runner chain on rate limit errors ([489944f](https://github.com/erikpr1994/stokowski/commit/489944fc87db4568b195585d893d0d1bd52d2249)), closes [#9](https://github.com/erikpr1994/stokowski/issues/9)
* generic schedule via external create_command ([50b153a](https://github.com/erikpr1994/stokowski/commit/50b153a041a03d480c27b281b5c70b64a95edde0)), closes [#12](https://github.com/erikpr1994/stokowski/issues/12)
* GitHub Issues as alternative tracker backend ([46df153](https://github.com/erikpr1994/stokowski/commit/46df153a26e7f6f2edaa0174f7b8187ca2827160)), closes [#11](https://github.com/erikpr1994/stokowski/issues/11)
* GitHub PR review events drive gate transitions ([d569b62](https://github.com/erikpr1994/stokowski/commit/d569b62236761c490115c0810972f7be71cbee91))
* GitHub PR status integration for gate transitions ([97ded33](https://github.com/erikpr1994/stokowski/commit/97ded33ad50cfff7115dd86d06f5ae3a41d36742)), closes [#7](https://github.com/erikpr1994/stokowski/issues/7)
* history UI + workflow recovery on restart ([#101](https://github.com/erikpr1994/stokowski/issues/101)) ([ddaec4d](https://github.com/erikpr1994/stokowski/commit/ddaec4de7fe6edf790bb83cc322b7c857d1bcdad))
* idempotent init — repairs existing config on re-run ([f26ca03](https://github.com/erikpr1994/stokowski/commit/f26ca036a31cae71f6d50e3d5f95ee175951ce1c))
* idempotent init — repairs existing config on re-run ([236f8a6](https://github.com/erikpr1994/stokowski/commit/236f8a6422e561302ed2f1c8d5df55408b158c77))
* inject last_run_at into Jinja2 template context ([335817c](https://github.com/erikpr1994/stokowski/commit/335817c5222f7abdc7fcfeb7fff07cd6e446b5a3))
* light/dark mode with system preference detection ([#74](https://github.com/erikpr1994/stokowski/issues/74)) ([d859b85](https://github.com/erikpr1994/stokowski/commit/d859b858516b28991d6e6c9e71d5e78cccb4dc27))
* move issue to terminal state and clean workspace on pipeline completion ([d4a239c](https://github.com/erikpr1994/stokowski/commit/d4a239c1691a19ac834f0e250cf3730d56967feb))
* multi-tracker support, webhooks, and generic scheduling ([0dd9be0](https://github.com/erikpr1994/stokowski/commit/0dd9be0e3c1793d57eb986a12115bf231679f300))
* parse pipeline config and stage files from WORKFLOW.md ([17213af](https://github.com/erikpr1994/stokowski/commit/17213af58278f8b5952906f17173dd95539e9ce7))
* pass LINEAR_TEAM_KEY to agent subprocess env ([481db82](https://github.com/erikpr1994/stokowski/commit/481db82f97d8eca08b8313a1ba031e8e1edc75e2))
* pass workflow.yaml Linear credentials to agent subprocesses ([770206c](https://github.com/erikpr1994/stokowski/commit/770206ce784026f9c93dffa03b6a0b3bda16aa7d))
* per-project concurrency limits for cross-project parallelization ([347e39c](https://github.com/erikpr1994/stokowski/commit/347e39c65ed522dcb7fd52bfd7e4690247bea285))
* per-workflow filtering and reduced polling with webhooks ([#49](https://github.com/erikpr1994/stokowski/issues/49)) ([9e8e870](https://github.com/erikpr1994/stokowski/commit/9e8e870408b57b31641d0ef57da7e9bf1b25b557))
* per-workflow linear_states, literal state names in linear_state ([#65](https://github.com/erikpr1994/stokowski/issues/65)) ([789714c](https://github.com/erikpr1994/stokowski/commit/789714c5b5d3d9e44e77d1c5dbdd50b6b74e569b))
* persistent state for crash recovery ([1b2398b](https://github.com/erikpr1994/stokowski/commit/1b2398b6efd7be78dbb7b698e47271b5e263e72f))
* persistent state for crash recovery ([a135c58](https://github.com/erikpr1994/stokowski/commit/a135c58965850d90c59ca9ff4880a5b865a992e4)), closes [#2](https://github.com/erikpr1994/stokowski/issues/2)
* PR-based dispatch + workflow name on agent cards ([#88](https://github.com/erikpr1994/stokowski/issues/88)) ([68d928b](https://github.com/erikpr1994/stokowski/commit/68d928b04665abd31d99821bc8486abac9100ff1)), closes [#87](https://github.com/erikpr1994/stokowski/issues/87)
* release-please automation, docs update, history filtering ([#105](https://github.com/erikpr1994/stokowski/issues/105)) ([8968824](https://github.com/erikpr1994/stokowski/commit/896882454c84a2dcdf6a49a0fb7150cf10029922))
* responsive UI for mobile/tablet ([#83](https://github.com/erikpr1994/stokowski/issues/83)) ([8b048fa](https://github.com/erikpr1994/stokowski/commit/8b048fa54ab02f01b449bb2318d5870f15a2e92f))
* run history + global Linear rate limiter + dispatch queue ([#97](https://github.com/erikpr1994/stokowski/issues/97)) ([644d4f4](https://github.com/erikpr1994/stokowski/commit/644d4f4589cca79c07e1d780db30ddaca967d2c5)), closes [#69](https://github.com/erikpr1994/stokowski/issues/69)
* schedule-only workflows, per-workflow filtering, description tracking ([#52](https://github.com/erikpr1994/stokowski/issues/52)) ([4b49141](https://github.com/erikpr1994/stokowski/commit/4b49141d57cf16a015d58f7e5df4f78f89527d9e)), closes [#51](https://github.com/erikpr1994/stokowski/issues/51)
* scheduled issue creation via cron expressions ([34efd9e](https://github.com/erikpr1994/stokowski/commit/34efd9e3d1455ec90d149a031c6470bfe454e349))
* shared root config for multi-workflow projects ([452438c](https://github.com/erikpr1994/stokowski/commit/452438ce5f8549ffa1e3a5643bc12d657aa4fbaa))
* shared root config for multi-workflow projects ([1ca1f70](https://github.com/erikpr1994/stokowski/commit/1ca1f709deb84bf94ad3528dab7b00fe24abc2ce))
* shared tracker client across all workflows + global rate limiter ([#95](https://github.com/erikpr1994/stokowski/issues/95)) ([99b12bf](https://github.com/erikpr1994/stokowski/commit/99b12bf1345c90784906f43ad59149e7d46016f5))
* show pending gates in web dashboard ([283b145](https://github.com/erikpr1994/stokowski/commit/283b1456f5d586c0f85e7418822d4114b72e1101))
* show pipeline stage and runner type in web dashboard ([5064a5b](https://github.com/erikpr1994/stokowski/commit/5064a5b2f44e991355d9880e1f2787afc29016a7))
* show workflow start time in dashboard tabs ([#100](https://github.com/erikpr1994/stokowski/issues/100)) ([f2c576d](https://github.com/erikpr1994/stokowski/commit/f2c576d84486206e54edcda83b06194be8cc6e1a))
* single daemon managing multiple workflows ([680da17](https://github.com/erikpr1994/stokowski/commit/680da177961ad2d12b4b554809f02367b4a80531))
* single daemon managing multiple workflows ([a391b82](https://github.com/erikpr1994/stokowski/commit/a391b822182551324ff4b44f995385dcfc7b46e4)), closes [#15](https://github.com/erikpr1994/stokowski/issues/15)
* start/stop individual workflows from dashboard ([#43](https://github.com/erikpr1994/stokowski/issues/43)) ([37c83ed](https://github.com/erikpr1994/stokowski/commit/37c83ed24d0cab9cdcdcf0d8ff2befb154d1a776))
* stokowski init command ([32c2f23](https://github.com/erikpr1994/stokowski/commit/32c2f2342e48be6672e711b09ab668d583b92982))
* stokowski init command to scaffold workflow config ([4a4c0f8](https://github.com/erikpr1994/stokowski/commit/4a4c0f88256669e5d2d0baa07e97b530774eb88f)), closes [#21](https://github.com/erikpr1994/stokowski/issues/21)
* store tracking data in issue description instead of comments ([#50](https://github.com/erikpr1994/stokowski/issues/50)) ([24ae8e4](https://github.com/erikpr1994/stokowski/commit/24ae8e477ae01333bdcc50d1e306ea1f80a20aa4))
* switch to append-only comment strategy in workflow ([56cecf9](https://github.com/erikpr1994/stokowski/commit/56cecf90cc5046dd8bae908ea25f9f8fb109b36b))
* team filtering, triage state, blocked status, label-based routing ([30505e1](https://github.com/erikpr1994/stokowski/commit/30505e18d0ac4e5657a33baee5ad4fe206a31c1c))
* terminal states support custom linear_state and "none" ([#64](https://github.com/erikpr1994/stokowski/issues/64)) ([3d71c34](https://github.com/erikpr1994/stokowski/commit/3d71c34d7d3f05b9e16f70bf89f1bf9ed6c01707))
* track per-issue last_run_at in orchestrator ([ea84374](https://github.com/erikpr1994/stokowski/commit/ea843740a250d057e2a5605463b10723e3a69267))
* unified multi-workflow dashboard with tab navigation ([3e9679e](https://github.com/erikpr1994/stokowski/commit/3e9679e087f9408152b647f4c69aaeb53c8f577b))
* unified multi-workflow dashboard with tab navigation ([7bf4931](https://github.com/erikpr1994/stokowski/commit/7bf4931c6b68c1b1d35859accb06e7ad7936225b)), closes [#16](https://github.com/erikpr1994/stokowski/issues/16)
* update rework flow to use PR comments and last_run_at ([886075d](https://github.com/erikpr1994/stokowski/commit/886075d2e35e47a66b9a1e529079c7efd5af9909))
* use GitHub releases API for update checks ([be1d872](https://github.com/erikpr1994/stokowski/commit/be1d87239290a05e8e986854c4953b68ad9585d7))
* web-based workflow config editor ([5caa8e2](https://github.com/erikpr1994/stokowski/commit/5caa8e26a630ebbc4a2b8a36b6f4221b787e032f))
* web-based workflow config editor ([1fa454e](https://github.com/erikpr1994/stokowski/commit/1fa454e0304ee4ed3dddcbb5193f34dd04476aab)), closes [#17](https://github.com/erikpr1994/stokowski/issues/17)
* webhook endpoint for instant Linear state change reactions ([166d590](https://github.com/erikpr1994/stokowski/commit/166d5909d66deb880224643002b01e653a5e2f51)), closes [#1](https://github.com/erikpr1994/stokowski/issues/1)
* workflows start stopped by default, webhook setup in init ([#45](https://github.com/erikpr1994/stokowski/issues/45)) ([aba37b8](https://github.com/erikpr1994/stokowski/commit/aba37b893a63ea51a324c605e9746d1fb44384c8))
* workspace_enabled=false skips worktree/clone creation ([#62](https://github.com/erikpr1994/stokowski/issues/62)) ([6c9ea43](https://github.com/erikpr1994/stokowski/commit/6c9ea43c1dfe3f5ad94f807d759c5f9d3a07b203))


### Bug Fixes

* _is_eligible uses pickup_states as eligible states ([#60](https://github.com/erikpr1994/stokowski/issues/60)) ([02b7d8d](https://github.com/erikpr1994/stokowski/commit/02b7d8dbac9d9ebec9bf0e87a79cf612e8a4ff77))
* _load_dotenv no longer overrides existing env vars ([47f6364](https://github.com/erikpr1994/stokowski/commit/47f6364ceeb8d1fedb6474afccd3656687c6bcfd))
* _load_dotenv no longer overrides existing env vars ([8d5377b](https://github.com/erikpr1994/stokowski/commit/8d5377b1a19f82f1dc4ea488430578fb55203d13)), closes [#4](https://github.com/erikpr1994/stokowski/issues/4)
* add traceback to worker error log for debugging ([#67](https://github.com/erikpr1994/stokowski/issues/67)) ([809ba26](https://github.com/erikpr1994/stokowski/commit/809ba26e938235e58b9e2cf2921273f3e5786cc2))
* add traceback to worker error log for debugging ([#68](https://github.com/erikpr1994/stokowski/issues/68)) ([918207a](https://github.com/erikpr1994/stokowski/commit/918207a18f2be04f42f699edbb805e4a8a00f517))
* address code review findings across orchestrator, runner, and web ([25bc28d](https://github.com/erikpr1994/stokowski/commit/25bc28d9eaac5b41aaaabf664489a135c135a012))
* auto-migrate old schedule format, manager survives workflow failures ([#42](https://github.com/erikpr1994/stokowski/issues/42)) ([d03ee9a](https://github.com/erikpr1994/stokowski/commit/d03ee9ad0924b8e383ab5fe278d16efc043083b3))
* check return value of update_issue_state at all call sites ([6347584](https://github.com/erikpr1994/stokowski/commit/6347584f6c01451017694ac9c1c00ac6c8397612))
* clear state on quit, add Run Now button to dashboard ([#54](https://github.com/erikpr1994/stokowski/issues/54)) ([23e421e](https://github.com/erikpr1994/stokowski/commit/23e421e0c3d43766768ab8a31b5c0ad3b50f6b32))
* define _is_synthetic in _transition, skip same-state moves ([#71](https://github.com/erikpr1994/stokowski/issues/71)) ([058cd34](https://github.com/erikpr1994/stokowski/commit/058cd3475d76c96761ca8d0c6dfc6354a65b0f21))
* description can be list, fix get_last_tracking_timestamp call ([#70](https://github.com/erikpr1994/stokowski/issues/70)) ([06e5cd5](https://github.com/erikpr1994/stokowski/commit/06e5cd5f579c6d138c5f763e4f049c19acc6fb69))
* don't close shared tracker client when stopping individual workflows ([#107](https://github.com/erikpr1994/stokowski/issues/107)) ([9629380](https://github.com/erikpr1994/stokowski/commit/962938038673a1dda7bf10be5d7bd4b4db9f2538))
* eliminate all unnecessary Linear API calls from orchestrator ([#99](https://github.com/erikpr1994/stokowski/issues/99)) ([55ac029](https://github.com/erikpr1994/stokowski/commit/55ac029f2847f36dff3b5319c7bef2ecc836a954))
* escape PR JSON in release-please auto-merge step ([#109](https://github.com/erikpr1994/stokowski/issues/109)) ([4a528c3](https://github.com/erikpr1994/stokowski/commit/4a528c37680d83b72cbb0922aad1f52ad38d5592))
* exclude prompts/ from setuptools package discovery ([de001b4](https://github.com/erikpr1994/stokowski/commit/de001b4b28880553eaedd1c5ae30e23b374c4451))
* file logging + don't cancel internally-tracked workers ([#81](https://github.com/erikpr1994/stokowski/issues/81)) ([9f0c5d6](https://github.com/erikpr1994/stokowski/commit/9f0c5d62b57d6bcf81b1134f7101883f871d25fd))
* github url in readme install instructions ([b4e3a81](https://github.com/erikpr1994/stokowski/commit/b4e3a81d11eb0fec505da1b75a19fdf3a403795f))
* guard all tracker calls for synthetic issues ([#58](https://github.com/erikpr1994/stokowski/issues/58)) ([62e8d99](https://github.com/erikpr1994/stokowski/commit/62e8d991ac39a9611819f0c3724922d50a3e60a3))
* handle 'already used by worktree' in second attempt too ([#91](https://github.com/erikpr1994/stokowski/issues/91)) ([5d00b3c](https://github.com/erikpr1994/stokowski/commit/5d00b3c603bcfc4a54c58937ab76db5660c5033e))
* improve /release command clarity for link refs and squash-merge notice ([b400dd4](https://github.com/erikpr1994/stokowski/commit/b400dd4a03044865fb384d0907efbfb863ca7f83))
* include lifecycle context in multi-turn continuation prompts ([ca82942](https://github.com/erikpr1994/stokowski/commit/ca82942fb6e671a1613ade9cd5ed981bc67f8695))
* increase Linear API min gap to 2.5s (1500/hour limit) ([#98](https://github.com/erikpr1994/stokowski/issues/98)) ([ce18abe](https://github.com/erikpr1994/stokowski/commit/ce18abed9381d67b58fec0e4815cc130210089fe))
* increase subprocess stdout buffer to 10MB to handle large NDJSON lines ([a346125](https://github.com/erikpr1994/stokowski/commit/a346125fa1917d9588779bc615919d2a28ab5fc7))
* init checks .env for missing/empty API key ([3be9e55](https://github.com/erikpr1994/stokowski/commit/3be9e55af4d3bb17ddc9ee5e497fcea8bde2833d))
* init checks .env for missing/empty API key and prompts to set it ([25f243d](https://github.com/erikpr1994/stokowski/commit/25f243dbe2442123df7f4bedb0aa339d26d0d4e2))
* init loads .stokowski/.env and validates all existing workflows ([202c880](https://github.com/erikpr1994/stokowski/commit/202c880b0d0e1a4584c5f1cd36491ff5daddde73))
* init loads .stokowski/.env and validates all existing workflows ([dcbd52a](https://github.com/erikpr1994/stokowski/commit/dcbd52a5ed5314291e8717fb4d6148ced8d9e08d))
* init prompts for API key, correct gitignore, creates root config ([ecc553e](https://github.com/erikpr1994/stokowski/commit/ecc553e07c4a474693b78a4b2ff57a75167a57ba))
* init prompts for API key, correct gitignore, creates root config ([1264273](https://github.com/erikpr1994/stokowski/commit/126427358fdfdfd8f88cdc4004cc9a39d4705415))
* init skips tracker prompts when already configured ([#44](https://github.com/erikpr1994/stokowski/issues/44)) ([752801d](https://github.com/erikpr1994/stokowski/commit/752801d6a2197e0d9928387f7e745fcfb4aca1a6))
* init skips workflow.yaml creation when workflows already exist ([a2e444c](https://github.com/erikpr1994/stokowski/commit/a2e444cdefeefee091c6d584562128a78edeb23e))
* init skips workflow.yaml when workflows already exist ([6cd25c6](https://github.com/erikpr1994/stokowski/commit/6cd25c6d0d6869b2f1ceecb0c3509533284570f4))
* invalidate tracker client when API key changes on hot-reload ([0f9c09c](https://github.com/erikpr1994/stokowski/commit/0f9c09c96ae3f830446f22f8ab6d378ac12a34e4))
* invalidate tracker client when API key changes on hot-reload ([f5755b2](https://github.com/erikpr1994/stokowski/commit/f5755b20259d743fd40bf4820ad30ade8a7d59e9)), closes [#5](https://github.com/erikpr1994/stokowski/issues/5)
* kill orphan agents on startup + handle CancelledError in manager ([#102](https://github.com/erikpr1994/stokowski/issues/102)) ([839bde8](https://github.com/erikpr1994/stokowski/commit/839bde8f86aed54af85c72703df99d0d42a696f8))
* light mode hardcoded colors + schedule validation for trackerless ([#76](https://github.com/erikpr1994/stokowski/issues/76)) ([0146255](https://github.com/erikpr1994/stokowski/commit/01462556069d8df0b984917b2e7d26313a44e17d))
* Linear 400 on state update — use team.states instead of workflowStates filter ([77a0bad](https://github.com/erikpr1994/stokowski/commit/77a0bad817c0869ef73889404290f4475f088df6))
* Linear API retry on 400/429 + fix-reviews PR number extraction ([#94](https://github.com/erikpr1994/stokowski/issues/94)) ([f677fac](https://github.com/erikpr1994/stokowski/commit/f677fac1f14c87ffaf9d1db528c7eae1b8a1aef6))
* make _SilentUndefined inherit from jinja2.Undefined ([1b6ddb3](https://github.com/erikpr1994/stokowski/commit/1b6ddb338194e79e5c831ba44fc34cbad36ebe7c))
* NameError in run_manager — workflow_paths renamed to root_cfg ([#41](https://github.com/erikpr1994/stokowski/issues/41)) ([0fbd45a](https://github.com/erikpr1994/stokowski/commit/0fbd45a2cf163adb561a482a3c67b8dd8e4d9640))
* poll pickup_states instead of active states, webhook polling optimization ([#53](https://github.com/erikpr1994/stokowski/issues/53)) ([e1419a3](https://github.com/erikpr1994/stokowski/commit/e1419a3b5b56c6be43edfa01103787f353f460a8))
* PR-based issues skip all Linear API calls ([#89](https://github.com/erikpr1994/stokowski/issues/89)) ([caf82c1](https://github.com/erikpr1994/stokowski/commit/caf82c10861dcd25175d3d5d024c7189ad21ea58))
* preserve workspace when issue is blocked ([#103](https://github.com/erikpr1994/stokowski/issues/103)) ([bc90fcf](https://github.com/erikpr1994/stokowski/commit/bc90fcf6c2165507bbeb15cba4c08352ba6d649c))
* prevent re-dispatch loop when gate state transition fails ([60f391f](https://github.com/erikpr1994/stokowski/commit/60f391ff02ddba55da7da81d931e02a33e47c960))
* prevent re-dispatch of completed issues + serialize worktree creation ([#75](https://github.com/erikpr1994/stokowski/issues/75)) ([d8be170](https://github.com/erikpr1994/stokowski/commit/d8be170078d1803dd11838c6292948e66255b339))
* read __version__ from package metadata instead of hardcoded string ([ae74016](https://github.com/erikpr1994/stokowski/commit/ae740169baa3b05f65b97d103b4be176da547dc4))
* reconciliation includes pickup_states as valid active states ([#61](https://github.com/erikpr1994/stokowski/issues/61)) ([844ee78](https://github.com/erikpr1994/stokowski/commit/844ee782c8b51ab20400b6f6165f21ec49e1a8e6))
* remove all HTML comments from Linear issues ([#63](https://github.com/erikpr1994/stokowski/issues/63)) ([72eb638](https://github.com/erikpr1994/stokowski/commit/72eb6389ecb706438032fae207177719c33cb6be))
* remove startup cleanup API calls, suppress httpx noise ([#59](https://github.com/erikpr1994/stokowski/issues/59)) ([d2aff9f](https://github.com/erikpr1994/stokowski/commit/d2aff9f7aaa64588f999afb29eb66a8cccf818ad))
* resolve critical review issues — gate claiming, duplicate comments, crash recovery, codex timeout ([8f2ac3f](https://github.com/erikpr1994/stokowski/commit/8f2ac3f43e2f905ae9972a08851b771aadca5fd5))
* reuse existing worktree when branch is already checked out ([#90](https://github.com/erikpr1994/stokowski/issues/90)) ([a511b5e](https://github.com/erikpr1994/stokowski/commit/a511b5e38decdf380b499bf7cb6f998b13e6a4e8))
* save webhook secret to config, stop stripping per-workflow overrides ([#84](https://github.com/erikpr1994/stokowski/issues/84)) ([71401d4](https://github.com/erikpr1994/stokowski/commit/71401d484ff5df1b317c1b127fe2ef7dd2dedb78))
* second WorkspaceResult also had wrong field names ([#93](https://github.com/erikpr1994/stokowski/issues/93)) ([135b867](https://github.com/erikpr1994/stokowski/commit/135b867fb8fdbb3554b353b557e70485a37a7296))
* single turn per dispatch in state machine mode ([ee8f0f6](https://github.com/erikpr1994/stokowski/commit/ee8f0f614b7c224f8ec557ac57638dcde5023c2e))
* skip already-tried runners in fallback chain ([35ed17f](https://github.com/erikpr1994/stokowski/commit/35ed17ffdac5a2bd5c051ea0d0f4317d0219f1f5))
* skip API calls for synthetic issues + reduce poll load ([#57](https://github.com/erikpr1994/stokowski/issues/57)) ([9d28b0e](https://github.com/erikpr1994/stokowski/commit/9d28b0e9d072914b813af3795ca7f3affccf2d0d))
* skip comment fetch for synthetic issues in prompt + light mode colors ([#77](https://github.com/erikpr1994/stokowski/issues/77)) ([8acc71c](https://github.com/erikpr1994/stokowski/commit/8acc71c98a88081e788f1fe8ff3ae7ace6510e3d))
* skip prompt scaffolding when workflows already exist ([2817c9a](https://github.com/erikpr1994/stokowski/commit/2817c9a5d66851229a10168a7a6b7562191da811))
* skip startup cleanup when tracker disabled, always show Run button ([#55](https://github.com/erikpr1994/stokowski/issues/55)) ([a3fc7e9](https://github.com/erikpr1994/stokowski/commit/a3fc7e9098909580fd3a2745971ab7e594e29443))
* specify ISO 8601 datetime format for comment headers ([e6b63b8](https://github.com/erikpr1994/stokowski/commit/e6b63b81beec1774d41adb4eb567d1c289e3d705))
* stop retry spam when no orchestrator slots available ([#79](https://github.com/erikpr1994/stokowski/issues/79)) ([1b2cf4e](https://github.com/erikpr1994/stokowski/commit/1b2cf4e25f71398556cdd2cb8fde5b6ceef896f7))
* stopped workflows cannot be ticked ([#72](https://github.com/erikpr1994/stokowski/issues/72)) ([bd92bee](https://github.com/erikpr1994/stokowski/commit/bd92bee7cf91aa9e15ce25af9537f97c0a48ccba))
* store state file next to workflow YAML, not in workspace root ([429b0ee](https://github.com/erikpr1994/stokowski/commit/429b0ee14b788de6a321e0a4bd75b38fcc8e4ffe))
* trigger_scheduled_run loads config if not yet started ([#56](https://github.com/erikpr1994/stokowski/issues/56)) ([7665dc6](https://github.com/erikpr1994/stokowski/commit/7665dc616a4b1eb632856ad40caccf93edfe88b8))
* use &lt;br/&gt; for line breaks in Mermaid node labels ([754711f](https://github.com/erikpr1994/stokowski/commit/754711f857d7c559fffa076f22462a4924910d46))
* webhook init — guided step-by-step with tunnel setup ([#48](https://github.com/erikpr1994/stokowski/issues/48)) ([1e051af](https://github.com/erikpr1994/stokowski/commit/1e051afdbfc5eb883981c1bebea3743211b45fbe))
* webhook init flow — show setup steps first, then ask for secret ([#47](https://github.com/erikpr1994/stokowski/issues/47)) ([c096e59](https://github.com/erikpr1994/stokowski/commit/c096e59ee0cf3e839fa3831c1e39e070e121a539))
* webhook secret in .env, URL in stokowski.yaml ([#85](https://github.com/erikpr1994/stokowski/issues/85)) ([889a07f](https://github.com/erikpr1994/stokowski/commit/889a07f6ca406771b2a1aae6bb2a0e329b58217b))
* workflows default to enabled: false in root config ([#46](https://github.com/erikpr1994/stokowski/issues/46)) ([45668fd](https://github.com/erikpr1994/stokowski/commit/45668fdfa5df24fa184f30400ad6702bac2eca95))
* workspace_enabled=false crashes, labels list in prompt template ([#66](https://github.com/erikpr1994/stokowski/issues/66)) ([1346fff](https://github.com/erikpr1994/stokowski/commit/1346fff6658fcb7c496cd7a06473c9588dc3982d))
* WorkspaceResult field names (branch→branch_name, created→created_now) ([#92](https://github.com/erikpr1994/stokowski/issues/92)) ([419519a](https://github.com/erikpr1994/stokowski/commit/419519ac20be84a412efa6e324735b74ed3843d1))


### Reverts

* remove Linear-specific schedule implementation ([1bc538c](https://github.com/erikpr1994/stokowski/commit/1bc538cb7cb98cf8d46756906e601dec4185ad8c)), closes [#13](https://github.com/erikpr1994/stokowski/issues/13)


### Documentation

* add global install via pipx, zsh quoting note ([1128055](https://github.com/erikpr1994/stokowski/commit/1128055c78c055e5e33a21bd2ce786cac21ea9e2))
* add global install via pipx, zsh quoting note ([4e52cbe](https://github.com/erikpr1994/stokowski/commit/4e52cbead7433d55babcce553e1df7e3ea680b2d))
* add pipeline config example and stage file examples ([da7d8bb](https://github.com/erikpr1994/stokowski/commit/da7d8bb5ea61ab8fc43436166a393ca14359e82d))
* add pipeline mode documentation to CLAUDE.md ([0b6857c](https://github.com/erikpr1994/stokowski/commit/0b6857c591aceb3fa13a750ef167c018dc984751))
* add prerequisites per runner section ([365274a](https://github.com/erikpr1994/stokowski/commit/365274a4c4f4048db30a9deba4564714fb6ba94c))
* add prerequisites per runner section to README ([9c06d7c](https://github.com/erikpr1994/stokowski/commit/9c06d7c74851c0a7439730bd4212798f7565e19d)), closes [#8](https://github.com/erikpr1994/stokowski/issues/8)
* add Releases section to README ([033b5fa](https://github.com/erikpr1994/stokowski/commit/033b5fa4abdc97ea6329b02706cba2c8313e2712))
* clarify that the workflow diagram is a configurable example ([f9879b6](https://github.com/erikpr1994/stokowski/commit/f9879b632b2da28e141f9869f73a539f660da1a2))
* comprehensive README update for all new features ([11d6397](https://github.com/erikpr1994/stokowski/commit/11d6397480e196f197b955eb9b2eb447ff359276))
* comprehensive README update for all new features ([c1c90b6](https://github.com/erikpr1994/stokowski/commit/c1c90b695563b5fe35a13cce3e7346f89758f2ec))
* convert ASCII flowcharts to Mermaid diagrams ([ca98ee2](https://github.com/erikpr1994/stokowski/commit/ca98ee29847b3ed0bc281475c88ef265789bb778))
* fix pipx upgrade instructions for local path installs ([f1d1363](https://github.com/erikpr1994/stokowski/commit/f1d1363b93e3c15bde400b4612bce66f4f0c559e))
* remove Releases section from README ([fd8b90e](https://github.com/erikpr1994/stokowski/commit/fd8b90e36be75e840b309e4335bddd45ad2878d8))
* replace SSH clone with gh CLI, update setup instructions ([48c2a06](https://github.com/erikpr1994/stokowski/commit/48c2a06137770750d51c9dc010ee43f12b621bac))
* update CLAUDE.md for state machine workflow model ([4775637](https://github.com/erikpr1994/stokowski/commit/47756372636a0ebcedd39f3bc9c83bb2ff21c332))
* update Emdash comparison and Symphony additions sections ([15d15d4](https://github.com/erikpr1994/stokowski/commit/15d15d48d31c682b15292a8dfe97f06630fe9d83))
* update README and CLAUDE.md with team filtering, triage, blocked, routing ([a5541bd](https://github.com/erikpr1994/stokowski/commit/a5541bdd2d6eddd0b404ff0ab36c05700d5c2ac4))
* update README for multi-runner support and fix transition key bug ([b18da0a](https://github.com/erikpr1994/stokowski/commit/b18da0a859151d5a0646b42ba5ea168d9614305e))
* update README for multi-tracker, webhooks, and scheduling ([16a48eb](https://github.com/erikpr1994/stokowski/commit/16a48eb617fefeefbb09226d56b97229c5a13b79))
* update README for multi-tracker, webhooks, and scheduling ([8c93af6](https://github.com/erikpr1994/stokowski/commit/8c93af6d128129a771e71bd8963495989550c7b7))
* update README for state machine workflow configuration ([d6c7ad3](https://github.com/erikpr1994/stokowski/commit/d6c7ad3233ff01008fbe1aff9b73cb6d3689dd48))
* update README intro to reflect improvements beyond Symphony ([a9ed097](https://github.com/erikpr1994/stokowski/commit/a9ed09751a8f0aa6a216961fcb88bb83fc493e40))
* update README to reflect comment story and rework context flow ([471b32b](https://github.com/erikpr1994/stokowski/commit/471b32b77a1c9bdb2b99552686de0fc5999db647))
* update Upgrading section to use latest tag and add pip upgrade path ([c4b0e01](https://github.com/erikpr1994/stokowski/commit/c4b0e017d21533ac9aa9c6978923a432ea1b2877))

## [Unreleased]

---

## [0.5.0] - 2026-04-10

### Added

- feat: multi-workflow manager — run N workflows from a shared `stokowski.yaml` root config with independent start/stop controls (manager.py) (99b12bf)
- feat: GitHub Issues tracker backend — label-based state management with automatic label creation and atomic swaps (github_issues.py) (68d928b)
- feat: PR-based dispatch — `source: github-prs` processes pull requests directly without a tracker (68d928b)
- feat: crash recovery — persist and restore per-issue state (stage, session ID, workspace path) on restart (2974b97)
- feat: run history — completed agent runs recorded to `history.json` and displayed in dashboard (644d4f4, ddaec4d)
- feat: shared Linear rate limiter — all workflows share one API client with semaphore throttling and exponential cooldown (99b12bf, ce18abe)
- feat: dispatch queue — candidate issues survive failed API ticks instead of being dropped (1baec5f)
- feat: per-workflow filtering — `filter_labels`, `exclude_labels`, `pickup_states`, per-workflow `linear_states` (4b49141, 2ae492b, 789714c)
- feat: schedule-only workflows — `tracker_enabled: false` with cron schedule, no tracker dependency (4b49141)
- feat: workspace-free workflows — `workspace_enabled: false` skips worktree/clone creation (6c9ea43)
- feat: light/dark mode with system preference detection, responsive layout, ARIA accessibility (d859b85, 8b048fa, ff351c3)
- feat: webhook init — `stokowski init` configures secrets for both Linear and GitHub with HMAC verification (9d412f1)
- feat: Gemini CLI runner with session resumption and stream-json parsing (runner.py)
- feat: orphan agent cleanup — kill stale `claude -p` processes on startup (839bde8)
- feat: terminal states support custom `linear_state` and `"none"` (3d71c34)
- feat: workflow start time shown in dashboard tabs (f2c576d)
- feat: manager state recovery — previously running workflows auto-restart (ddaec4d)

### Fixed

- fix: preserve workspace when issue is blocked — unpushed WIP survives (bc90fcf)
- fix: CancelledError during shutdown on Python 3.14 — `task.cancelled()` guard (839bde8)
- fix: worktree reuse when branch is already checked out (a511b5e, 5d00b3c)
- fix: reconciliation includes pickup_states as valid active states (844ee78)
- fix: PR-based issues skip all Linear API calls (caf82c1, 62e8d99)
- fix: stopped workflows cannot be ticked by webhooks or poll loops (bd92bee)
- fix: Linear API retry on 400/429 with exponential backoff (f677fac)
- fix: serialize worktree creation with asyncio.Lock (d8be170)
- fix: prevent re-dispatch of completed issues (d8be170)
- fix: eliminate unnecessary Linear API calls from orchestrator (55ac029)
- fix: WorkspaceResult field names (419519a, 135b867)
- fix: webhook secret storage in .env and stokowski.yaml (889a07f, 71401d4)
- fix: light mode hardcoded colors and schedule validation (0146255)
- fix: file logging + don't cancel internally-tracked workers (9f0c5d6)
- fix: stop retry spam when no orchestrator slots available (1b2cf4e)
- fix: remove all HTML comments from Linear issues (72eb638)

---

## [0.4.0] - 2026-03-23

### Added

- feat: pass workflow.yaml Linear credentials (`api_key`, `project_slug`, `endpoint`) to agent subprocesses as env vars — agents now use the same Linear credentials as Stokowski without relying on shell environment (770206c)

### Changed

- docs: workflow.yaml is now the single source of truth for Linear credentials — removed `.env.example` and updated README setup guide (a9ed097)
- docs: update README intro to position Stokowski as building beyond Symphony (a9ed097)

---

## [0.3.0] - 2026-03-15

### Added

- feat: add todo state — pick up issues from Todo and move to In Progress automatically (94b9d02)

### Fixed

- fix: single turn per dispatch in state machine mode — agents no longer blow past stage boundaries (ee8f0f6)
- fix: prevent re-dispatch loop when gate state transition fails — keep issue claimed and retry (60f391f)
- fix: include lifecycle context in multi-turn continuation prompts (ca82942)
- fix: increase subprocess stdout buffer to 10MB to handle large NDJSON lines (a346125)
- fix: check return value of `update_issue_state` at all call sites (6347584)
- fix: Linear 400 on state update — use `team.states` instead of `workflowStates` filter (77a0bad)
- fix: make `_SilentUndefined` inherit from `jinja2.Undefined` (1b6ddb3)
- fix: read `__version__` from package metadata instead of hardcoded string (ae74016)

---

## [0.2.2] - 2026-03-15

### Added

- feat: add todo state — pick up issues from Todo and move to In Progress automatically (94b9d02)

### Fixed

- fix: read `__version__` from package metadata instead of hardcoded string — update checker now shows correct version (ae74016)

---

## [0.2.1] - 2026-03-15

### Fixed

- fix: exclude `prompts/` from setuptools package discovery — fresh installs failed with "Multiple top-level packages" error (de001b4)
- fix: `project.license` deprecation warning — switched to SPDX string format (de001b4)

### Changed

- docs: rewrite Emdash comparison for accuracy — now an open-source desktop app with 22+ agent CLIs (15d15d4)
- docs: expand "What Stokowski adds beyond Symphony" with state machine, multi-runner, and prompt assembly sections (15d15d4)
- docs: clarify workflow diagram is a configurable example, not a fixed pipeline (f9879b6)

---

## [0.2.0] - 2026-03-13

### Added

- feat: configurable state machine workflows replacing fixed staged pipeline (`config.py`, `orchestrator.py`) (c0109d9)
- feat: three-layer prompt assembly — global prompt + stage prompt + lifecycle injection (`prompt.py`) (a2d61fd)
- feat: multi-runner support — Claude Code and Codex configurable per-state (`runner.py`) (8ff0e74)
- feat: gate protocol with "Gate Approved" / "Rework" Linear states and `max_rework` escalation (`orchestrator.py`) (b100531)
- feat: structured state tracking via HTML comments on Linear issues (`tracking.py`) (1a684c4)
- feat: Linear comment creation, comment fetching, and issue state mutation methods (`linear.py`) (e475351)
- feat: `on_stage_enter` lifecycle hook (`config.py`) (c5852c4)
- feat: Codex runner stall detection and timeout handling (`runner.py`) (db58f04)
- feat: pipeline completion moves issues to terminal state and cleans workspace (`orchestrator.py`) (d4a239c)
- feat: pending gates and runner type shown in web dashboard (`web.py`) (283b145, 5064a5b)
- feat: pipeline stage config dataclasses and validation (`config.py`) (8b769d8, a4dd34d)
- docs: example `workflow.yaml` and `prompts/*.example.md` files (da63359, da7d8bb)

### Fixed

- fix: gate claiming, duplicate comments, crash recovery, codex timeout (8f2ac3f)
- fix: transition key mismatch — example config used `success`, orchestrator expected `complete` (b18da0a)
- fix: use `<br/>` for line breaks in Mermaid node labels (754711f)

### Changed

- refactor: `WORKFLOW.md` (YAML front matter + prompt body) replaced by `workflow.yaml` + `prompts/` directory (c0109d9)
- refactor: `TrackerConfig.active_states` / `terminal_states` replaced by `LinearStatesConfig` mapping (c0109d9)
- refactor: `RunAttempt.stage` renamed to `state_name`, `runner_type` field removed (f0ccd48)
- refactor: web dashboard updated for state machine field names (09a7fa8)
- refactor: CLI auto-detects `workflow.yaml` → `workflow.yml` → `WORKFLOW.md` (0a8df54)
- docs: README rewritten for state machine model, multi-runner support, config reference (d6c7ad3, b18da0a)
- docs: CLAUDE.md updated for state machine workflow model (4775637)

### Chores

- chore: add `workflow.yaml`, `workflow.yml`, and `prompts/*.md` to `.gitignore` (59cb69e)

---

## [0.1.0] - 2026-03-08

### Added

- Async orchestration loop polling Linear for issues in configurable states
- Per-issue isolated git workspace lifecycle with `after_create`, `before_run`, `after_run`, `before_remove` hooks
- Claude Code CLI integration with `--output-format stream-json` streaming and multi-turn `--resume` sessions
- Exponential backoff retry and stall detection
- State reconciliation — running agents cancelled when Linear issue moves to terminal state
- Optional FastAPI web dashboard with live agent status
- Rich terminal UI with persistent status bar and single-key controls
- Jinja2 prompt templates with full issue context
- `.env` auto-load and `$VAR` env references in config
- Hot-reload of `WORKFLOW.md` on every poll tick
- Per-state concurrency limits
- `--dry-run` mode for config validation without dispatching agents
- Startup update check with footer indicator
- `last_run_at` template variable injected into agent prompts for rework timestamp filtering
- Append-only Linear comment strategy (planning + completion comment per run)

---

[Unreleased]: https://github.com/erikpr1994/stokowski/compare/v0.5.0...HEAD
[0.5.0]: https://github.com/erikpr1994/stokowski/releases/tag/v0.5.0
[0.4.0]: https://github.com/erikpr1994/stokowski/releases/tag/v0.4.0
[0.3.0]: https://github.com/erikpr1994/stokowski/releases/tag/v0.3.0
[0.2.2]: https://github.com/erikpr1994/stokowski/releases/tag/v0.2.2
[0.2.1]: https://github.com/erikpr1994/stokowski/releases/tag/v0.2.1
[0.2.0]: https://github.com/erikpr1994/stokowski/releases/tag/v0.2.0
[0.1.0]: https://github.com/erikpr1994/stokowski/releases/tag/v0.1.0
