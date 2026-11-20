# Sets the Jako wallpaper ("Fill"). Windows Settings > Personalisation > Background changes it back any time.
param([string]$Image)
$dir = Join-Path ([Environment]::GetFolderPath('MyPictures')) 'Jako'
New-Item -ItemType Directory -Force $dir | Out-Null
$target = Join-Path $dir 'jako-wallpaper.jpg'
Copy-Item -Force $Image $target
Set-ItemProperty -Path 'HKCU:\Control Panel\Desktop' -Name WallpaperStyle -Value '10'
Set-ItemProperty -Path 'HKCU:\Control Panel\Desktop' -Name TileWallpaper -Value '0'
Add-Type -TypeDefinition @"
using System.Runtime.InteropServices;
public class JakoWall { [DllImport("user32.dll", CharSet = CharSet.Unicode)]
  public static extern int SystemParametersInfo(int a, int b, string c, int d); }
"@
[void][JakoWall]::SystemParametersInfo(20, 0, $target, 3)
Write-Host "Wallpaper set: $target"
