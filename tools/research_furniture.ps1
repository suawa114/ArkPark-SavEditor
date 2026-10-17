param([string]$Package = '/Game/ArkPark/Blueprints/建造系统/BP_拉面龙家具', [string]$Label = 'furniture', [string]$Batch, [string]$Match, [switch]$Inventory, [switch]$ActorInventory, [switch]$AllActorNames, [switch]$ComfortCalls, [string]$Pattern = '舒适')
$ErrorActionPreference = 'Stop'
$taskAssemblyDir = Join-Path $PSScriptRoot '../build/research/FModel/assemblies'
[void][Reflection.Assembly]::LoadFrom((Resolve-Path "$taskAssemblyDir/CUE4Parse.dll").Path)
[void][Reflection.Assembly]::LoadFrom((Resolve-Path "$taskAssemblyDir/Newtonsoft.Json.dll").Path)
[CUE4Parse.Compression.OodleHelper]::Initialize((Resolve-Path "$PSScriptRoot/../build/research/oodle/bin/oodle-data-shared.dll").Path)
$taskVersion = [CUE4Parse.UE4.Versions.VersionContainer]::new([CUE4Parse.UE4.Versions.EGame]::GAME_UE5_6, 0, [Activator]::CreateInstance([CUE4Parse.UE4.Versions.FPackageFileVersion]), $null, $null, $null)
$taskProvider = [CUE4Parse.FileProvider.DefaultFileProvider]::new('D:\Tools\Steam\steamapps\common\ArkPark2\ArkPark2\Content\Paks', [IO.SearchOption]::TopDirectoryOnly, $taskVersion, [StringComparer]::OrdinalIgnoreCase)
Add-Type -ReferencedAssemblies @((Resolve-Path "$taskAssemblyDir/CUE4Parse.dll").Path, (Resolve-Path "$taskAssemblyDir/Serilog.dll").Path, [Console].Assembly.Location) -TypeDefinition @'
using System;
using CUE4Parse.MappingsProvider;
public class ResearchMappings : ITypeMappingsProvider {
    public TypeMappings MappingsForGame {get;} = new TypeMappings();
    public void Load(string path, StringComparer c) {throw new NotSupportedException();}
    public void Load(byte[] data, StringComparer c) {throw new NotSupportedException();}
    public void Reload() {}
}
public class ResearchSink : Serilog.Core.ILogEventSink {
    public void Emit(Serilog.Events.LogEvent e) { Console.WriteLine(e.RenderMessage() + " " + e.Exception); }
}
'@
if ($Batch) { [Serilog.Log]::Logger = [Serilog.LoggerConfiguration]::new().CreateLogger() }
else { [Serilog.Log]::Logger = [Serilog.LoggerConfiguration]::new().WriteTo.Sink([ResearchSink]::new()).CreateLogger() }
$taskMappings = [ResearchMappings]::new()
foreach ($taskNative in @('Transform','TimerHandle','DataTableRowHandle','UserDefinedEnum','BlueprintGeneratedClass','UserDefinedStruct','Function','DataTable','SceneComponent','SCS_Node','SimpleConstructionScript','InheritableComponentHandler')) {
    $taskMappings.MappingsForGame.Types.Add($taskNative, [CUE4Parse.MappingsProvider.Struct]::new($taskMappings.MappingsForGame, $taskNative, $null, [Collections.Generic.Dictionary[int,CUE4Parse.MappingsProvider.PropertyInfo]]::new(), 0))
}
function Add-ResearchProperty($owner, $index, $name, $type, $struct = $null) {
    $taskPropertyType = [CUE4Parse.MappingsProvider.PropertyType]::new($type, $struct, $null, $null, $null, $null, $null)
    $taskMappings.MappingsForGame.Types[$owner].Properties[$index] = [CUE4Parse.MappingsProvider.PropertyInfo]::new($index, $name, $taskPropertyType, 1)
    $taskMappings.MappingsForGame.Types[$owner].PropertyCount = [Math]::Max($taskMappings.MappingsForGame.Types[$owner].PropertyCount, $index+1)
}
# Research-only native schema hypotheses; output must be independently validated.
Add-ResearchProperty 'TimerHandle' 0 'Handle' 'UInt64Property'
Add-ResearchProperty 'Transform' 0 'Rotation' 'StructProperty' 'Quat'
Add-ResearchProperty 'Transform' 1 'Translation' 'StructProperty' 'Vector'
Add-ResearchProperty 'Transform' 2 'Scale3D' 'StructProperty' 'Vector'
$taskMappings.MappingsForGame.Types.Add('LevelStreaming', [CUE4Parse.MappingsProvider.Struct]::new($taskMappings.MappingsForGame, 'LevelStreaming', $null, [Collections.Generic.Dictionary[int,CUE4Parse.MappingsProvider.PropertyInfo]]::new(), 0))
foreach ($taskStreamingType in @('LevelStreamingAlwaysLoaded','LevelStreamingDynamic')) {
    $taskMappings.MappingsForGame.Types.Add($taskStreamingType, [CUE4Parse.MappingsProvider.Struct]::new($taskMappings.MappingsForGame, $taskStreamingType, 'LevelStreaming', [Collections.Generic.Dictionary[int,CUE4Parse.MappingsProvider.PropertyInfo]]::new(), 0))
}
Add-ResearchProperty 'LevelStreaming' 0 'WorldAsset' 'SoftObjectProperty'
Add-ResearchProperty 'UserDefinedStruct' 0 'Status' 'ByteProperty'
Add-ResearchProperty 'UserDefinedStruct' 1 'Guid' 'StructProperty' 'Guid'
Add-ResearchProperty 'DataTable' 0 'RowStruct' 'ObjectProperty'
Add-ResearchProperty 'DataTableRowHandle' 0 'DataTable' 'ObjectProperty'
Add-ResearchProperty 'DataTableRowHandle' 1 'RowName' 'NameProperty'
$taskEnumMap = [CUE4Parse.MappingsProvider.PropertyType]::new('MapProperty',$null,[CUE4Parse.MappingsProvider.PropertyType]::new('NameProperty',$null,$null,$null,$null,$null,$null),[CUE4Parse.MappingsProvider.PropertyType]::new('TextProperty',$null,$null,$null,$null,$null,$null),$null,$null,$null)
$taskMappings.MappingsForGame.Types['UserDefinedEnum'].Properties[0] = [CUE4Parse.MappingsProvider.PropertyInfo]::new(0,'DisplayNameMap',$taskEnumMap,1)
Add-ResearchProperty 'BlueprintGeneratedClass' 0 'NumReplicatedProperties' 'IntProperty'
Add-ResearchProperty 'BlueprintGeneratedClass' 1 'bHasNativizedParent' 'BoolProperty'
Add-ResearchProperty 'BlueprintGeneratedClass' 2 'bHasCookedComponentInstancingData' 'BoolProperty'
foreach ($taskSlot in @(3,4,5)) {
    $taskType = [CUE4Parse.MappingsProvider.PropertyType]::new('ArrayProperty', $null, [CUE4Parse.MappingsProvider.PropertyType]::new('ObjectProperty',$null,$null,$null,$null,$null,$null),$null,$null,$null,$null)
    $taskMappings.MappingsForGame.Types['BlueprintGeneratedClass'].Properties[$taskSlot] = [CUE4Parse.MappingsProvider.PropertyInfo]::new($taskSlot, @('DynamicBindingObjects','ComponentTemplates','Timelines')[$taskSlot-3], $taskType, 1)
}
Add-ResearchProperty 'BlueprintGeneratedClass' 7 'SimpleConstructionScript' 'ObjectProperty'
Add-ResearchProperty 'BlueprintGeneratedClass' 8 'SimpleConstructionScript' 'ObjectProperty'
Add-ResearchProperty 'BlueprintGeneratedClass' 9 'InheritableComponentHandler' 'ObjectProperty'
Add-ResearchProperty 'BlueprintGeneratedClass' 10 'UberGraphFunction' 'ObjectProperty'
Add-ResearchProperty 'BlueprintGeneratedClass' 11 'UberGraphFunction' 'ObjectProperty'
$taskProvider.MappingsContainer = $taskMappings
$taskProvider.ReadScriptData = $true
$taskProvider.Initialize()
[void]$taskProvider.Mount()
$taskProvider.PostMount()
if ($Batch) {
    [Serilog.Log]::Logger = [Serilog.LoggerConfiguration]::new().CreateLogger()
    $taskResults = [Collections.Generic.List[object]]::new()
    $taskProvider.ReadScriptData = [bool]$ComfortCalls
    foreach ($taskPath in (Get-Content $Batch -Raw | ConvertFrom-Json)) {
        $taskPkg = $taskProvider.LoadPackage($taskPath)
        if ($ComfortCalls) {
            foreach ($taskLazy in $taskPkg.ExportsLazy) {
                $taskObject = $taskLazy.Value
                if ($taskObject -isnot [CUE4Parse.UE4.Objects.UObject.UStruct]) {continue}
                $taskJson = [Newtonsoft.Json.JsonConvert]::SerializeObject($taskObject)
                if ($taskJson -match $Pattern) {$taskResults.Add(@{path=$taskPath; export=($taskJson | ConvertFrom-Json)})}
            }
            continue
        }
        $taskClass = $null
        for ($taskI=0; $taskI -lt $taskPkg.ExportMap.Length; $taskI++) {
            $taskName = $taskPkg.NameMap[$taskPkg.ExportMap[$taskI].ObjectName.NameIndex].Name
            if ($taskName.EndsWith('_C') -and !$taskName.StartsWith('Default__')) { $taskClass=$taskPkg.ExportsLazy[$taskI].Value; break }
        }
        if ($null -eq $taskClass -or $null -eq $taskClass.ClassDefaultObject) { continue }
        $taskDefault = $taskClass.ClassDefaultObject.Load()
        $taskAncestors = [Collections.Generic.List[object]]::new()
        $taskParent = $taskClass
        for ($taskDepth=0; $taskDepth -lt 20; $taskDepth++) {
            if ($null -eq $taskParent.SuperStruct) {break}
            $taskParent = $taskParent.SuperStruct.Load()
            if ($taskParent -isnot [CUE4Parse.UE4.Objects.Engine.UBlueprintGeneratedClass]) {break}
            $taskParentDefault = $taskParent.ClassDefaultObject.Load()
            $taskAncestors.Add(@{blueprint=([Newtonsoft.Json.JsonConvert]::SerializeObject($taskParent) | ConvertFrom-Json); defaults=([Newtonsoft.Json.JsonConvert]::SerializeObject($taskParentDefault) | ConvertFrom-Json)})
        }
        $taskResults.Add(@{path=$taskPath; blueprint=([Newtonsoft.Json.JsonConvert]::SerializeObject($taskClass) | ConvertFrom-Json); defaults=([Newtonsoft.Json.JsonConvert]::SerializeObject($taskDefault) | ConvertFrom-Json); ancestors=$taskAncestors})
    }
    ConvertTo-Json -InputObject $taskResults -Depth 100 | Set-Content -Encoding utf8 "$PSScriptRoot/../build/research/batch.json"
    return
}
$taskPackage = $taskProvider.LoadPackage($Package)
if ($ActorInventory) {
    [Serilog.Log]::Logger = [Serilog.LoggerConfiguration]::new().CreateLogger()
    $taskResults = [Collections.Generic.List[object]]::new()
    $taskProvider.ReadScriptData = $false
    for ($taskIndex=0; $taskIndex -lt $taskPackage.ExportsLazy.Length; $taskIndex++) {
        $taskName = $taskPackage.NameMap[$taskPackage.ExportMap[$taskIndex].ObjectName.NameIndex].Name
        if (!$AllActorNames -and $taskName -notmatch 'BP_|ChildActor.*_CAT') {continue}
        $taskObject = $taskPackage.ExportsLazy[$taskIndex].Value
        if ($taskObject -is [CUE4Parse.UE4.Objects.UObject.UStruct]) {continue}
        $taskClass = $taskObject.Class.Load()
        $taskAncestors = [Collections.Generic.List[string]]::new()
        $taskIsFurniture = $false
        for ($taskDepth=0; $taskDepth -lt 20; $taskDepth++) {
            if ($null -eq $taskClass) {break}
            $taskAncestors.Add($taskClass.Name)
            if ($taskClass.Name -eq 'BP_拉面龙家具_C') {$taskIsFurniture=$true; break}
            if ($taskClass -isnot [CUE4Parse.UE4.Objects.Engine.UBlueprintGeneratedClass] -or $null -eq $taskClass.SuperStruct) {break}
            $taskClass = $taskClass.SuperStruct.Load()
        }
        if ($taskIsFurniture) {
            $taskResults.Add(@{index=$taskIndex; ancestors=$taskAncestors; actor=([Newtonsoft.Json.JsonConvert]::SerializeObject($taskObject) | ConvertFrom-Json)})
        }
    }
    ConvertTo-Json -InputObject $taskResults -Depth 100 | Set-Content -Encoding utf8 "$PSScriptRoot/../build/research/$Label-actors.json"
    return
}
if ($Inventory) {
    for ($taskIndex=0; $taskIndex -lt $taskPackage.ExportMap.Length; $taskIndex++) {
        "$taskIndex $($taskPackage.NameMap[$taskPackage.ExportMap[$taskIndex].ObjectName.NameIndex].Name)"
    }
    return
}
for ($taskIndex=0; $taskIndex -lt $taskPackage.ExportsLazy.Length; $taskIndex++) {
    if ($Match -and $taskPackage.NameMap[$taskPackage.ExportMap[$taskIndex].ObjectName.NameIndex].Name -notmatch $Match) {continue}
    try {
        $taskExport = $taskPackage.ExportsLazy[$taskIndex].Value
        "$taskIndex $($taskExport.Name) $($taskExport.ExportType)"
        if ($taskExport -is [CUE4Parse.UE4.Objects.UObject.UStruct]) {
            @($taskExport.ChildProperties) | Where-Object { $null -ne $_ } | ForEach-Object { "  $($_.Name) $($_.GetType().Name)" }
        }
        [Newtonsoft.Json.JsonConvert]::SerializeObject($taskExport, [Newtonsoft.Json.Formatting]::Indented) | Set-Content -Encoding utf8 "$PSScriptRoot/../build/research/$Label-$taskIndex.json"
    } catch { "$taskIndex ERROR: $($_.Exception.Message)" }
}



