# Changelog

## [0.1.17](https://github.com/dcc-mcp/dcc-mcp-3dsmax/compare/v0.1.16...v0.1.17) (2026-06-08)


### Code Refactoring

* **max:** convert register_builtin_actions to shared phase pipeline ([22f0eee](https://github.com/dcc-mcp/dcc-mcp-3dsmax/commit/22f0eee8fd67692a1c6f05f55981589285e07902))

## [0.1.16](https://github.com/dcc-mcp/dcc-mcp-3dsmax/compare/v0.1.15...v0.1.16) (2026-06-06)


### Bug Fixes

* update justfile max-install-core-win to use dcc-mcp-core&gt;=0.18.7 ([cde6bbe](https://github.com/dcc-mcp/dcc-mcp-3dsmax/commit/cde6bbe845ff12cda4835de122b17352f543d032))

## [0.1.15](https://github.com/dcc-mcp/dcc-mcp-3dsmax/compare/v0.1.14...v0.1.15) (2026-06-06)


### Bug Fixes

* **ci:** isolate workflow_dispatch from push concurrency in release workflow ([5b42de4](https://github.com/dcc-mcp/dcc-mcp-3dsmax/commit/5b42de4e079e9dec6ff545e035cf88ebe2542467))
* **ci:** isolate workflow_dispatch from push concurrency in release workflow ([2e59c01](https://github.com/dcc-mcp/dcc-mcp-3dsmax/commit/2e59c01ec27c9103b85ff65c7b7240a0d69d337c))

## [0.1.14](https://github.com/dcc-mcp/dcc-mcp-3dsmax/compare/v0.1.13...v0.1.14) (2026-06-06)


### Bug Fixes

* align 0.18.2 docs and dev install references ([dbd500d](https://github.com/dcc-mcp/dcc-mcp-3dsmax/commit/dbd500d8e5e04c3e7c0a9eb51a051e06c05d159c))
* update three-state to six-state readiness references ([70be049](https://github.com/dcc-mcp/dcc-mcp-3dsmax/commit/70be04979b2ce9c3a19ccd76a8fd14ef4fc7bb60))
* update three-state to six-state readiness references in llms-full.txt and server.py ([671ac8c](https://github.com/dcc-mcp/dcc-mcp-3dsmax/commit/671ac8ccb4e59a3028524103fded74c113a5b226))


### Documentation

* refresh 0.18.2 runtime and sidecar references ([#72](https://github.com/dcc-mcp/dcc-mcp-3dsmax/issues/72)) ([ea26396](https://github.com/dcc-mcp/dcc-mcp-3dsmax/commit/ea263961064e7597f49035dcd4ad5830642a1324))

## [0.1.13](https://github.com/dcc-mcp/dcc-mcp-3dsmax/compare/v0.1.12...v0.1.13) (2026-06-06)


### Features

* align 3ds Max adapter to dcc-mcp-core==0.17.56 / dcc-mcp-server==0.17.56 ([#71](https://github.com/dcc-mcp/dcc-mcp-3dsmax/issues/71)) ([07da04e](https://github.com/dcc-mcp/dcc-mcp-3dsmax/commit/07da04e92d83112d89722ff866dd2ae90f556f68))


### Documentation

* add AI agent docs (AGENTS.md, llms.txt, llms-full.txt, CLAUDE.md, GEMINI.md) ([541e4a8](https://github.com/dcc-mcp/dcc-mcp-3dsmax/commit/541e4a82262724d934a2ab5bd4c74b7f4589f56e))

## [0.1.12](https://github.com/dcc-mcp/dcc-mcp-3dsmax/compare/v0.1.11...v0.1.12) (2026-06-03)


### Features

* **setup:** install 3dsmax startup hook ([44e8032](https://github.com/dcc-mcp/dcc-mcp-3dsmax/commit/44e8032733b529c1d3b5de8792ff7cff95dd9658))


### Documentation

* **setup:** use absolute install skill paths ([3ab5984](https://github.com/dcc-mcp/dcc-mcp-3dsmax/commit/3ab5984f39a5781de72dd84f8bb3735d8cadec87))

## [0.1.11](https://github.com/dcc-mcp/dcc-mcp-3dsmax/compare/v0.1.10...v0.1.11) (2026-06-03)


### Features

* add intelligent recall metadata to all 15 bundled 3ds Max skills ([1cde52a](https://github.com/dcc-mcp/dcc-mcp-3dsmax/commit/1cde52abcd91a95935f5e40329dd2e51fffb56f6)), closes [#52](https://github.com/dcc-mcp/dcc-mcp-3dsmax/issues/52)
* add intelligent recall metadata to all 15 bundled 3ds Max skills ([74b71b4](https://github.com/dcc-mcp/dcc-mcp-3dsmax/commit/74b71b4fd932290ab6699e613d724543058ff5c5)), closes [#52](https://github.com/dcc-mcp/dcc-mcp-3dsmax/issues/52)
* **sidecar:** use core launch lifecycle contract ([e20ea8f](https://github.com/dcc-mcp/dcc-mcp-3dsmax/commit/e20ea8f10e0e47b507ee8825549f7cc4b6a930c4))


### Bug Fixes

* add remove_menu() to clean up DCC MCP menu on uninstall ([cb75f48](https://github.com/dcc-mcp/dcc-mcp-3dsmax/commit/cb75f48a5922c0870c4141282ce3d66f40d41619))
* correct role/risk misclassifications and populate produces/targets metadata ([853d12e](https://github.com/dcc-mcp/dcc-mcp-3dsmax/commit/853d12e13ec57955cbf50aa2e22ec3de9caf8423))
* correct role/risk misclassifications and populate produces/targets metadata ([5f29e23](https://github.com/dcc-mcp/dcc-mcp-3dsmax/commit/5f29e23920ee4ecece358e220dbe42a168ab9b7b))
* delete persisted .mcr files in remove_menu for permanent macro removal ([a179d5c](https://github.com/dcc-mcp/dcc-mcp-3dsmax/commit/a179d5c0f6ee1aaf40d797f297331a82af416e9f))


### Documentation

* add image to README ([7390bcd](https://github.com/dcc-mcp/dcc-mcp-3dsmax/commit/7390bcda6a219aaba1abfd6dec3965bbe253a49a))
* add readme screenshot image ([4bcaabc](https://github.com/dcc-mcp/dcc-mcp-3dsmax/commit/4bcaabce04461c2ed6bfe95b8ecef5e5bc6a3fd9))
* make readme hero image responsive with direct asset link ([11c9370](https://github.com/dcc-mcp/dcc-mcp-3dsmax/commit/11c9370fc41056f977ae07b87e558dc734f2011e))
* refine README hero sizing and bilingual intro ([913e75a](https://github.com/dcc-mcp/dcc-mcp-3dsmax/commit/913e75aeb9a09c0742fce8c31f26e0585a049de9))
* refresh README layout with responsive hero section ([93fa0fa](https://github.com/dcc-mcp/dcc-mcp-3dsmax/commit/93fa0fa1fd59c22da34281202a0b24089440d9b2))
* rename readme screenshot asset to formal name ([aca67cd](https://github.com/dcc-mcp/dcc-mcp-3dsmax/commit/aca67cd478d3357e247a3f44e65b9755edd9dd51))
* tune hero image sizing for responsive README layout ([09c658c](https://github.com/dcc-mcp/dcc-mcp-3dsmax/commit/09c658c5b60b741ebc3bb919983ce32ccda76ffc))

## [0.1.10](https://github.com/loonghao/dcc-mcp-3dsmax/compare/v0.1.9...v0.1.10) (2026-06-01)


### Bug Fixes

* resolve [#50](https://github.com/loonghao/dcc-mcp-3dsmax/issues/50) — prevent ValueError when both dispatcher and execution_bridge are passed ([ae4e7fa](https://github.com/loonghao/dcc-mcp-3dsmax/commit/ae4e7fa6fee1e5641f6249bb03f83ee1c1483fdc))

## [0.1.9](https://github.com/loonghao/dcc-mcp-3dsmax/compare/v0.1.8...v0.1.9) (2026-05-29)


### Features

* add latest dcc-mcp-core integrations, new skills, and agent install ([14107c8](https://github.com/loonghao/dcc-mcp-3dsmax/commit/14107c8c4a3a3b012bc7fa3d2b056188873b65ca))
* latest dcc-mcp-core integrations, new skills + agent install ([11bba84](https://github.com/loonghao/dcc-mcp-3dsmax/commit/11bba84263507554629e3ceb973deeb38bf7d0a1))
* latest dcc-mcp-core integrations, new skills + agent install ([#54](https://github.com/loonghao/dcc-mcp-3dsmax/issues/54)) ([11bba84](https://github.com/loonghao/dcc-mcp-3dsmax/commit/11bba84263507554629e3ceb973deeb38bf7d0a1))


### Bug Fixes

* normalize status→success in _run_skill_script, fix test/server API mismatches ([6148bfe](https://github.com/loonghao/dcc-mcp-3dsmax/commit/6148bfefab4222953fb0b1c943e345d755317156))

## [0.1.8](https://github.com/loonghao/dcc-mcp-3dsmax/compare/v0.1.7...v0.1.8) (2026-05-26)


### Features

* add asset readiness validation tools ([f62fc82](https://github.com/loonghao/dcc-mcp-3dsmax/commit/f62fc82f6ec221dfef8d59476247b3bc4ec0b59f)), closes [#29](https://github.com/loonghao/dcc-mcp-3dsmax/issues/29)
* add camera lighting tools ([02f8ae1](https://github.com/loonghao/dcc-mcp-3dsmax/commit/02f8ae1f2b03a425245fae607f4245502312529b)), closes [#31](https://github.com/loonghao/dcc-mcp-3dsmax/issues/31)
* add display metadata tools ([ea49078](https://github.com/loonghao/dcc-mcp-3dsmax/commit/ea4907800422d80c93d10ccfeb0d6d10fb552bbf)), closes [#30](https://github.com/loonghao/dcc-mcp-3dsmax/issues/30)
* add geometry import export tools ([e2ce27b](https://github.com/loonghao/dcc-mcp-3dsmax/commit/e2ce27bf832ce0a915e7aecb48c085e711f50b66)), closes [#22](https://github.com/loonghao/dcc-mcp-3dsmax/issues/22)
* add mesh cleanup normal tools ([a33b791](https://github.com/loonghao/dcc-mcp-3dsmax/commit/a33b79108d6aef24e40311fdbacfd5c051d93cb2)), closes [#23](https://github.com/loonghao/dcc-mcp-3dsmax/issues/23)
* add render viewport tools ([2bc778e](https://github.com/loonghao/dcc-mcp-3dsmax/commit/2bc778ed0f6b54a0d0dbf437e782074317d07aa8)), closes [#26](https://github.com/loonghao/dcc-mcp-3dsmax/issues/26)
* add rigging deformer tools ([dcac64b](https://github.com/loonghao/dcc-mcp-3dsmax/commit/dcac64b6ed1628cb669f03b6c1f286fd54e99190)), closes [#28](https://github.com/loonghao/dcc-mcp-3dsmax/issues/28)
* add safe scripting introspection tools ([4f57e41](https://github.com/loonghao/dcc-mcp-3dsmax/commit/4f57e4157492093795f1e60f0f68da8556027d47)), closes [#20](https://github.com/loonghao/dcc-mcp-3dsmax/issues/20)
* add uv atlas tools ([52af40d](https://github.com/loonghao/dcc-mcp-3dsmax/commit/52af40da407d3ab1a55991f9ed5d035a4cbcd3e6)), closes [#24](https://github.com/loonghao/dcc-mcp-3dsmax/issues/24)
* expand animation timeline tools ([42e3944](https://github.com/loonghao/dcc-mcp-3dsmax/commit/42e39442fe03d87b952a4868674d378910b84821)), closes [#27](https://github.com/loonghao/dcc-mcp-3dsmax/issues/27)
* expand material map tools ([ef033f7](https://github.com/loonghao/dcc-mcp-3dsmax/commit/ef033f78d06c2f698ce38912262601c0099fabc5)), closes [#25](https://github.com/loonghao/dcc-mcp-3dsmax/issues/25)
* expand scene object management tools ([1a1768b](https://github.com/loonghao/dcc-mcp-3dsmax/commit/1a1768b94e05833584e004330eff0a3334323635)), closes [#21](https://github.com/loonghao/dcc-mcp-3dsmax/issues/21)


### Bug Fixes

* align 3dsmax core 0.17.34 ([618599f](https://github.com/loonghao/dcc-mcp-3dsmax/commit/618599fd43335cb481521e72a69858f955e78caf)), closes [#17](https://github.com/loonghao/dcc-mcp-3dsmax/issues/17) [#18](https://github.com/loonghao/dcc-mcp-3dsmax/issues/18)
* use core dispatcher abstractions for 3ds Max ([efb3930](https://github.com/loonghao/dcc-mcp-3dsmax/commit/efb3930a893ad12f2b6abde3234f0d72b7d02205))

## [0.1.7](https://github.com/loonghao/dcc-mcp-3dsmax/compare/v0.1.6...v0.1.7) (2026-05-23)


### Bug Fixes

* align 3dsmax runtime defaults ([616bcbc](https://github.com/loonghao/dcc-mcp-3dsmax/commit/616bcbc3cb61144d76f83e001b2dc89587c514c2))
* restore 3dsmax runtime startup ([72d9e8d](https://github.com/loonghao/dcc-mcp-3dsmax/commit/72d9e8d84cd1eefcc7820743a26de8febcb14349))


### Code Refactoring

* template mzp scripts ([84082b6](https://github.com/loonghao/dcc-mcp-3dsmax/commit/84082b68fec456a2e53730119855a66b23241d81))

## [0.1.6](https://github.com/loonghao/dcc-mcp-3dsmax/compare/v0.1.5...v0.1.6) (2026-05-23)


### Bug Fixes

* clean install lifecycle and autostart sidecar ([17eb473](https://github.com/loonghao/dcc-mcp-3dsmax/commit/17eb4738f30c1906c7d965e13b590e93ac6a1fd8))

## [0.1.5](https://github.com/loonghao/dcc-mcp-3dsmax/compare/v0.1.4...v0.1.5) (2026-05-23)


### Bug Fixes

* surface 3dsmax sidecar diagnostics ([38e319b](https://github.com/loonghao/dcc-mcp-3dsmax/commit/38e319b2716e65c030e410fdbac9c31d39446df5))

## [0.1.4](https://github.com/loonghao/dcc-mcp-3dsmax/compare/v0.1.3...v0.1.4) (2026-05-22)


### Features

* support versioned installer bootstrap ([230dcab](https://github.com/loonghao/dcc-mcp-3dsmax/commit/230dcabbbf09b97bd7902b97d30647acf31cb6d9))

## [0.1.3](https://github.com/loonghao/dcc-mcp-3dsmax/compare/v0.1.2...v0.1.3) (2026-05-21)


### Features

* support install and uninstall actions in mzp drag dialog ([0600579](https://github.com/loonghao/dcc-mcp-3dsmax/commit/060057904d2f416852623336e6eb4086e92c6862))


### Bug Fixes

* stop sidecar before mzp uninstall ([b8b5c70](https://github.com/loonghao/dcc-mcp-3dsmax/commit/b8b5c704cd17040fc4fe3abe03f1221fff8dc55f))

## [0.1.2](https://github.com/loonghao/dcc-mcp-3dsmax/compare/v0.1.1...v0.1.2) (2026-05-20)


### Features

* align 3dsmax sidecar with latest core ([a626fa0](https://github.com/loonghao/dcc-mcp-3dsmax/commit/a626fa06d7f0368401c4666d4b4459b0bc5aafa0))


### Bug Fixes

* tighten 3dsmax sidecar startup ([3b78607](https://github.com/loonghao/dcc-mcp-3dsmax/commit/3b78607c38a52f644176c7ef2415fa147447a4a6))

## [0.1.1](https://github.com/loonghao/dcc-mcp-3dsmax/compare/v0.1.0...v0.1.1) (2026-05-17)


### Features

* add max-dev-build-link-core-win (align with Maya/Blender) ([de06a52](https://github.com/loonghao/dcc-mcp-3dsmax/commit/de06a521352862aba5bcabe08905c80bef3db281))
* align with dcc-mcp-core 0.17.2 API (add diagnostics/execution options) ([2d220ad](https://github.com/loonghao/dcc-mcp-3dsmax/commit/2d220ad00fe4c85168764af3ca32372774b8557e))
* initial 3ds max adapter ([e3f2175](https://github.com/loonghao/dcc-mcp-3dsmax/commit/e3f2175a5faa124b74f9bf0d37d29bed9be4a034))

## [0.1.0] - 2026-05-16

### Added
- Initial release of dcc-mcp-3dsmax
- Basic MCP server implementation for 3ds Max
- Version detection via `pymxs.runtime.maxVersion()`
- Skill management (discover, load, unload)
- Environment variable configuration
- Basic API helpers for skill development
- Example `3dsmax-scene` skill
