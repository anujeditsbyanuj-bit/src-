{ pkgs }: {
  deps = [
    pkgs.python310
    pkgs.ffmpeg
    pkgs.mediainfo
    pkgs.aria2
    pkgs.megatools
    pkgs.cacert
    pkgs.p7zip
    pkgs.git
    pkgs.nodejs   # was pkgs.nodejs_20 — that attr doesn't exist in this channel's nixpkgs snapshot
    # pkgs.unrar
    # pkgs.rar
  ];
  env = {
  };
}
