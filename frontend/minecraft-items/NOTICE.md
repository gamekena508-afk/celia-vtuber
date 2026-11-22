# Minecraft item sprites (HUD)

- Source: TinyTank800/MinecraftAllImages (https://github.com/TinyTank800/MinecraftAllImages), commit
  `8888a461f088c6b907f13c14f25a12d90a481ae3`, legacy v1 catalog, layered up to Minecraft 1.21.10. The repository code is MIT licensed
  (Copyright (c) 2025 TinyTank800). It states the item images are captured in-game with a Fabric export mod, are not AI generated,
  and that it is not affiliated with Mojang or Microsoft.
- The MIT license of that repository covers its code. It does not transfer ownership of the Minecraft textures: the item art
  belongs to Mojang AB / Microsoft ("Minecraft" is a trademark of Mojang AB). These files are used locally, unmodified, only as
  inventory icons inside the owner's own Célia overlay. They are not sold and are not redistributed as a standalone asset pack.
  Review the Minecraft Usage Guidelines before any public redistribution of this folder.
- Reproduce / audit: `.venv/bin/python tools/minecraft_sprites/build_sprites.py` (pinned ZIP SHA-256 in
  `tools/minecraft_sprites/zip-sha256.json`; per-file SHA-256 in `1.21.10/manifest.json`).
- The HUD never downloads sprites at runtime and never draws a substitute: a missing sprite shows an empty slot.
