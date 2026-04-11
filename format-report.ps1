param(
    [string]$DocumentPath = "C:\Users\soumy\Downloads\SMART ATTENDANCE SYSTEM.docx"
)

$ErrorActionPreference = "Stop"

function Normalize-Text {
    param([string]$Text)
    if ($null -eq $Text) { return "" }
    $value = $Text -replace "[`r`n`a`f`v]", " "
    $value = $value -replace "\s+", " "
    $value.Trim()
}

function Ensure-Backup {
    param([string]$Path)
    $dir = Split-Path -Parent $Path
    $name = [System.IO.Path]::GetFileNameWithoutExtension($Path)
    $ext = [System.IO.Path]::GetExtension($Path)
    $backup = Join-Path $dir ("{0} - backup{1}" -f $name, $ext)
    Copy-Item -LiteralPath $Path -Destination $backup -Force
    return $backup
}

function Apply-Heading {
    param(
        $Paragraph,
        [string]$StyleName,
        [int]$FontSize
    )

    $Paragraph.Range.Style = $StyleName
    $Paragraph.Range.Font.Name = "Times New Roman"
    $Paragraph.Range.Font.Size = $FontSize
    $Paragraph.Range.Font.Bold = 1
    $Paragraph.Format.Alignment = 0
    $Paragraph.Format.LineSpacingRule = 0
    $Paragraph.Format.SpaceBefore = 12
    $Paragraph.Format.SpaceAfter = 12
}

function Apply-Body {
    param(
        $Paragraph,
        [int]$Alignment = 3
    )

    $Paragraph.Range.Style = "Normal"
    $Paragraph.Range.Font.Name = "Times New Roman"
    $Paragraph.Range.Font.Size = 12
    $Paragraph.Range.Font.Bold = 0
    $Paragraph.Format.Alignment = $Alignment
    $Paragraph.Format.LineSpacingRule = 1
    $Paragraph.Format.SpaceBefore = 0
    $Paragraph.Format.SpaceAfter = 6
}

function Get-Level {
    param([string]$Text)

    if ($Text -match '^\d+\.\d+\.\d+\s+') { return 3 }
    if ($Text -match '^\d+\.\d+\s+') { return 2 }
    if ($Text -eq 'INTRODUCTION') { return 1 }
    if ($Text -match '^\d+\.\s+') { return 1 }
    return 0
}

$frontMatterHeadings = @(
    "APPROVAL CERTIFICATE",
    "Approval Certificate",
    "ACKNOWLEDGEMENT",
    "ABSTRACT",
    "TABLE OF CONTENTS",
    "Table of Contents",
    "LIST OF FIGURES",
    "List of Figures",
    "LIST OF TABLES",
    "List of Tables"
)

$wdHeaderFooterPrimary = 1
$wdAlignPageNumberCenter = 1

if (-not (Test-Path -LiteralPath $DocumentPath)) {
    throw "Document not found: $DocumentPath"
}

$backupPath = Ensure-Backup -Path $DocumentPath

$word = $null
$doc = $null

try {
    Write-Output "Opening document..."
    $word = New-Object -ComObject Word.Application
    $word.Visible = $false
    $word.DisplayAlerts = 0
    $doc = $word.Documents.Open($DocumentPath, $false, $false)

    Write-Output "Applying margins..."
    foreach ($section in $doc.Sections) {
        $section.PageSetup.TopMargin = 72
        $section.PageSetup.BottomMargin = 72
        $section.PageSetup.LeftMargin = 72
        $section.PageSetup.RightMargin = 72
    }

    Write-Output "Rebuilding TOC..."
    $tocIndex = 0
    $lofIndex = 0
    for ($i = 1; $i -le $doc.Paragraphs.Count; $i++) {
        $text = Normalize-Text $doc.Paragraphs.Item($i).Range.Text
        if (-not $tocIndex -and $text -in @("Table of Contents", "TABLE OF CONTENTS")) {
            $tocIndex = $i
        }
        if (-not $lofIndex -and $text -in @("List of Figures", "LIST OF FIGURES")) {
            $lofIndex = $i
        }
        if ($tocIndex -and $lofIndex) { break }
    }

    if ($tocIndex -and $lofIndex -and $lofIndex -gt $tocIndex) {
        $tocHeading = $doc.Paragraphs.Item($tocIndex)
        $tocHeading.Range.Text = "TABLE OF CONTENTS`r"
        $tocHeading.Range.Style = "TOC Heading"
        $tocHeading.Range.Font.Name = "Times New Roman"
        $tocHeading.Range.Font.Size = 14
        $tocHeading.Range.Font.Bold = 1
        $tocHeading.Format.Alignment = 0
        $tocHeading.Format.SpaceBefore = 12
        $tocHeading.Format.SpaceAfter = 12

        $start = $tocHeading.Range.End
        $finish = $doc.Paragraphs.Item($lofIndex).Range.Start
        if ($finish -gt $start) {
            $range = $doc.Range($start, $finish)
            $range.Text = ""
            $insert = $doc.Range($tocHeading.Range.End, $tocHeading.Range.End)
            if ($doc.TablesOfContents.Count -gt 0) {
                while ($doc.TablesOfContents.Count -gt 0) {
                    $doc.TablesOfContents.Item(1).Delete()
                }
            }
            $null = $doc.TablesOfContents.Add($insert, $true, 1, 3)
        }
    }

    Write-Output "Formatting paragraphs..."
    $chapterCounter = 0
    $bodyStarted = $false
    $listMode = $false

    for ($i = 1; $i -le $doc.Paragraphs.Count; $i++) {
        $p = $doc.Paragraphs.Item($i)
        $text = Normalize-Text $p.Range.Text

        if ($i -lt 33) { continue }
        if ([string]::IsNullOrWhiteSpace($text)) { $listMode = $false; continue }
        if ([bool]$p.Range.Information(12)) { continue }

        if ($text -in $frontMatterHeadings) {
            switch ($text) {
                "Approval Certificate" { $p.Range.Text = "APPROVAL CERTIFICATE`r"; $text = "APPROVAL CERTIFICATE" }
                "Table of Contents" { $p.Range.Text = "TABLE OF CONTENTS`r"; $text = "TABLE OF CONTENTS" }
                "List of Figures" { $p.Range.Text = "LIST OF FIGURES`r"; $text = "LIST OF FIGURES" }
                "List of Tables" { $p.Range.Text = "LIST OF TABLES`r"; $text = "LIST OF TABLES" }
            }
            if ($text -eq "TABLE OF CONTENTS") {
                $p.Range.Style = "TOC Heading"
                $p.Range.Font.Name = "Times New Roman"
                $p.Range.Font.Size = 14
                $p.Range.Font.Bold = 1
                $p.Format.Alignment = 0
                $p.Format.SpaceBefore = 12
                $p.Format.SpaceAfter = 12
            } else {
                Apply-Heading -Paragraph $p -StyleName "Heading 1" -FontSize 14
            }
            $listMode = $false
            continue
        }

        $level = Get-Level -Text $text
        if (-not $bodyStarted -and ($text -eq "INTRODUCTION" -or $text -eq "1. INTRODUCTION")) {
            $bodyStarted = $true
        }

        if ($bodyStarted -and $level -eq 1) {
            $chapterCounter++
            $title = ($text -replace '^\d+\.\s*', '').ToUpper()
            $newText = "$chapterCounter. $title"
            if ($text -ne $newText) {
                $p.Range.Text = "$newText`r"
                $text = $newText
            }
            Apply-Heading -Paragraph $p -StyleName "Heading 1" -FontSize 14
            $listMode = $false
            continue
        }

        if ($level -eq 2) {
            Apply-Heading -Paragraph $p -StyleName "Heading 2" -FontSize 12
            $listMode = $false
            continue
        }

        if ($level -eq 3) {
            Apply-Heading -Paragraph $p -StyleName "Heading 3" -FontSize 12
            $listMode = $false
            continue
        }

        if ($text -match '^(Figure|Table)\s*\d+') {
            $p.Range.Style = "Caption"
            $p.Range.Font.Name = "Times New Roman"
            $p.Range.Font.Size = 12
            $p.Range.Font.Bold = 0
            $p.Format.Alignment = 1
            $p.Format.LineSpacingRule = 0
            $p.Format.SpaceBefore = 6
            $p.Format.SpaceAfter = 6
            $listMode = $false
            continue
        }

        if ($text -match ':$') {
            Apply-Body -Paragraph $p -Alignment 3
            $listMode = $true
            continue
        }

        if ($listMode -and $text.Length -le 120 -and $text -notmatch '[\.\:]$' -and $text -notmatch '^\d') {
            Apply-Body -Paragraph $p -Alignment 0
            try { $p.Range.ListFormat.ApplyBulletDefault() } catch {}
            continue
        }

        $listMode = $false
        Apply-Body -Paragraph $p -Alignment 3
    }

    Write-Output "Formatting tables..."
    foreach ($table in $doc.Tables) {
        $table.Range.Font.Name = "Times New Roman"
        $table.Range.Font.Size = 12
        $table.Range.ParagraphFormat.SpaceAfter = 0
        try { $table.Rows.Item(1).Range.Bold = 1 } catch {}
    }

    Write-Output "Adding page numbers..."
    foreach ($section in $doc.Sections) {
        $section.PageSetup.DifferentFirstPageHeaderFooter = $true
        try {
            while ($section.Footers.Item($wdHeaderFooterPrimary).PageNumbers.Count -gt 0) {
                $section.Footers.Item($wdHeaderFooterPrimary).PageNumbers.Item(1).Delete()
            }
        } catch {}
        $null = $section.Footers.Item($wdHeaderFooterPrimary).PageNumbers.Add($wdAlignPageNumberCenter, $true)
        $section.Footers.Item($wdHeaderFooterPrimary).Range.ParagraphFormat.Alignment = 1
    }

    Write-Output "Updating TOC and saving..."
    foreach ($toc in $doc.TablesOfContents) {
        $toc.Update()
    }

    $doc.Save()
    Write-Output "Formatted document saved."
    Write-Output "Backup: $backupPath"
    Write-Output ("Pages: {0}" -f $doc.ComputeStatistics(2))
}
finally {
    if ($doc) { $doc.Close() }
    if ($word) { $word.Quit() }
}
