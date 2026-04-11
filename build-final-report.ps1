$ErrorActionPreference = 'Stop'

function Normalize-Text([string]$Text) {
    if ($null -eq $Text) { return '' }
    $v = $Text.Replace("`r", ' ').Replace("`n", ' ')
    $v = $v -replace '\s+', ' '
    return $v.Trim()
}

$src = 'C:\Users\soumy\Downloads\SMART ATTENDANCE SYSTEM - backup.docx'
$dst = 'C:\Users\soumy\Downloads\SMART ATTENDANCE SYSTEM - final.docx'
Copy-Item -LiteralPath $src -Destination $dst -Force

$mapping = Get-Content 'report_replacements.json' -Raw | ConvertFrom-Json
$replaceMap = @{}
foreach($item in $mapping){ if($item.old){ $replaceMap[$item.old] = $item.new } }

$wdAlignParagraphLeft = 0
$wdAlignParagraphCenter = 1
$wdAlignParagraphJustify = 3
$wdLineSpaceSingle = 0
$wdLineSpace1pt5 = 1
$wdHeaderFooterPrimary = 1
$wdAlignPageNumberCenter = 1

$word = $null
$doc = $null
try {
    $word = New-Object -ComObject Word.Application
    $word.Visible = $false
    $word.DisplayAlerts = 0
    $doc = $word.Documents.Open($dst, $false, $false)

    foreach($section in $doc.Sections){
        $section.PageSetup.TopMargin = 72
        $section.PageSetup.BottomMargin = 72
        $section.PageSetup.LeftMargin = 72
        $section.PageSetup.RightMargin = 72
        $section.PageSetup.DifferentFirstPageHeaderFooter = $false
    }

    $normal = $doc.Styles.Item('Normal')
    $normal.Font.Name = 'Times New Roman'
    $normal.Font.Size = 12
    $normal.ParagraphFormat.Alignment = $wdAlignParagraphJustify
    $normal.ParagraphFormat.LineSpacingRule = $wdLineSpace1pt5
    $normal.ParagraphFormat.SpaceBefore = 0
    $normal.ParagraphFormat.SpaceAfter = 6

    foreach($styleName in @('Heading 1','Heading 2','Heading 3','TOC Heading','Caption')){
        try { $null = $doc.Styles.Item($styleName) } catch {}
    }
    $h1 = $doc.Styles.Item('Heading 1')
    $h1.Font.Name = 'Times New Roman'; $h1.Font.Size = 14; $h1.Font.Bold = 1
    $h1.ParagraphFormat.Alignment = $wdAlignParagraphLeft; $h1.ParagraphFormat.LineSpacingRule = $wdLineSpaceSingle; $h1.ParagraphFormat.SpaceBefore = 12; $h1.ParagraphFormat.SpaceAfter = 12
    $h2 = $doc.Styles.Item('Heading 2')
    $h2.Font.Name = 'Times New Roman'; $h2.Font.Size = 12; $h2.Font.Bold = 1
    $h2.ParagraphFormat.Alignment = $wdAlignParagraphLeft; $h2.ParagraphFormat.LineSpacingRule = $wdLineSpaceSingle; $h2.ParagraphFormat.SpaceBefore = 12; $h2.ParagraphFormat.SpaceAfter = 12
    $h3 = $doc.Styles.Item('Heading 3')
    $h3.Font.Name = 'Times New Roman'; $h3.Font.Size = 12; $h3.Font.Bold = 1
    $h3.ParagraphFormat.Alignment = $wdAlignParagraphLeft; $h3.ParagraphFormat.LineSpacingRule = $wdLineSpaceSingle; $h3.ParagraphFormat.SpaceBefore = 12; $h3.ParagraphFormat.SpaceAfter = 12
    $toch = $doc.Styles.Item('TOC Heading')
    $toch.Font.Name = 'Times New Roman'; $toch.Font.Size = 14; $toch.Font.Bold = 1
    $toch.ParagraphFormat.Alignment = $wdAlignParagraphLeft; $toch.ParagraphFormat.LineSpacingRule = $wdLineSpaceSingle; $toch.ParagraphFormat.SpaceBefore = 12; $toch.ParagraphFormat.SpaceAfter = 12
    $cap = $doc.Styles.Item('Caption')
    $cap.Font.Name = 'Times New Roman'; $cap.Font.Size = 12; $cap.Font.Bold = 0
    $cap.ParagraphFormat.Alignment = $wdAlignParagraphCenter; $cap.ParagraphFormat.LineSpacingRule = $wdLineSpaceSingle; $cap.ParagraphFormat.SpaceBefore = 6; $cap.ParagraphFormat.SpaceAfter = 6

    for($i=1; $i -le $doc.Paragraphs.Count; $i++){
        $p = $doc.Paragraphs.Item($i)
        $text = Normalize-Text $p.Range.Text
        if($replaceMap.ContainsKey($text)){
            $p.Range.Text = $replaceMap[$text] + "`r"
        }
    }

    $tocIndex = 0
    for($i=1; $i -le $doc.Paragraphs.Count; $i++){
        $text = Normalize-Text $doc.Paragraphs.Item($i).Range.Text
        if(-not $tocIndex -and $text -eq 'Table of Contents'){ $tocIndex = $i }
        if($tocIndex){ break }
    }
    if($tocIndex){
        $tocPara = $doc.Paragraphs.Item($tocIndex)
        $tocPara.Range.Text = "TABLE OF CONTENTS`r"
        $tocPara.Range.Style = 'TOC Heading'
    }
    if($doc.TablesOfContents.Count -gt 0){
        $toc = $doc.TablesOfContents.Item(1)
        $toc.UseHeadingStyles = $true
        $toc.UpperHeadingLevel = 1
        $toc.LowerHeadingLevel = 2
    }

    $frontMatter = @('APPROVAL CERTIFICATE','Approval Certificate','ACKNOWLEDGEMENT','ABSTRACT','TABLE OF CONTENTS','Table of Contents','LIST OF FIGURES','List of Figures','LIST OF TABLES','List of Tables')
    $bodyStarted = $false
    $listMode = $false
    for($i=1; $i -le $doc.Paragraphs.Count; $i++){
        $p = $doc.Paragraphs.Item($i)
        $text = Normalize-Text $p.Range.Text
        if([string]::IsNullOrWhiteSpace($text)){ $listMode = $false; continue }
        if($p.Range.Information(12)){ continue }

        $p.Range.Font.Name = 'Times New Roman'
        $p.Range.Font.Size = 12
        $p.Range.Font.Bold = 0

        if($text -in $frontMatter){
            if($text -eq 'Approval Certificate'){ $p.Range.Text = "APPROVAL CERTIFICATE`r"; $text = 'APPROVAL CERTIFICATE' }
            if($text -eq 'List of Figures'){ $p.Range.Text = "LIST OF FIGURES`r"; $text = 'LIST OF FIGURES' }
            if($text -eq 'List of Tables'){ $p.Range.Text = "LIST OF TABLES`r"; $text = 'LIST OF TABLES' }
            if($text -eq 'Table of Contents'){ $p.Range.Text = "TABLE OF CONTENTS`r"; $text = 'TABLE OF CONTENTS' }
            $p.Range.Style = $(if($text -eq 'TABLE OF CONTENTS'){'TOC Heading'} else {'Heading 1'})
            $p.Format.Alignment = $wdAlignParagraphLeft; $p.Format.LineSpacingRule = $wdLineSpaceSingle; $p.Format.SpaceBefore = 12; $p.Format.SpaceAfter = 12
            $p.Range.Font.Name = 'Times New Roman'; $p.Range.Font.Bold = 1; $p.Range.Font.Size = 14
            $listMode = $false
            continue
        }

        if($text -eq 'INTRODUCTION'){ $p.Range.Text = "1. INTRODUCTION`r"; $text = '1. INTRODUCTION' }
        if($text -eq '1. INTRODUCTION'){ $bodyStarted = $true }

        if($bodyStarted -and $text -match '^\d+\.\s+'){
            $title = ($text -replace '^\d+\.\s*','').ToUpper()
            $num = [regex]::Match($text,'^\d+').Value
            $p.Range.Text = "$num. $title`r"
            $p.Range.Style = 'Heading 1'
            $p.Format.Alignment = $wdAlignParagraphLeft; $p.Format.LineSpacingRule = $wdLineSpaceSingle; $p.Format.SpaceBefore = 12; $p.Format.SpaceAfter = 12
            $p.Range.Font.Name = 'Times New Roman'; $p.Range.Font.Bold = 1; $p.Range.Font.Size = 14
            $listMode = $false
            continue
        }
        if($text -match '^\d+\.\d+\.\d+\s+'){
            $p.Range.Style = 'Heading 3'
            $p.Format.Alignment = $wdAlignParagraphLeft; $p.Format.LineSpacingRule = $wdLineSpaceSingle; $p.Format.SpaceBefore = 12; $p.Format.SpaceAfter = 12
            $p.Range.Font.Name = 'Times New Roman'; $p.Range.Font.Bold = 1; $p.Range.Font.Size = 12
            $listMode = $false
            continue
        }
        if($text -match '^\d+\.\d+\s+'){
            $p.Range.Style = 'Heading 2'
            $p.Format.Alignment = $wdAlignParagraphLeft; $p.Format.LineSpacingRule = $wdLineSpaceSingle; $p.Format.SpaceBefore = 12; $p.Format.SpaceAfter = 12
            $p.Range.Font.Name = 'Times New Roman'; $p.Range.Font.Bold = 1; $p.Range.Font.Size = 12
            $listMode = $false
            continue
        }
        if($text -match '^(Figure|Table)\s*\d+'){
            $p.Range.Style = 'Caption'
            $p.Format.Alignment = $wdAlignParagraphCenter; $p.Format.LineSpacingRule = $wdLineSpaceSingle; $p.Format.SpaceBefore = 6; $p.Format.SpaceAfter = 6
            $listMode = $false
            continue
        }

        $p.Range.Style = 'Normal'
        $p.Format.Alignment = $wdAlignParagraphJustify; $p.Format.LineSpacingRule = $wdLineSpace1pt5; $p.Format.SpaceBefore = 0; $p.Format.SpaceAfter = 6
        try { $p.Range.ListFormat.RemoveNumbers() } catch {}

        if($text -match ':$'){
            $listMode = $true
            continue
        }

        if($listMode -and $text.Length -le 140 -and $text -notmatch '[\.:]$' -and $text -notmatch '^\d'){
            $p.Format.Alignment = $wdAlignParagraphLeft
            try { $p.Range.ListFormat.ApplyBulletDefault() } catch {}
            continue
        }

        $listMode = $false
    }

    foreach($table in $doc.Tables){
        $table.Range.Font.Name = 'Times New Roman'
        $table.Range.Font.Size = 12
        try { $table.Rows.Item(1).Range.Bold = 1 } catch {}
    }

    foreach($section in $doc.Sections){
        $footer = $section.Footers.Item($wdHeaderFooterPrimary)
        try { while($footer.PageNumbers.Count -gt 0){ $footer.PageNumbers.Item(1).Delete() } } catch {}
        $footer.Range.ParagraphFormat.Alignment = $wdAlignParagraphCenter
        $null = $footer.PageNumbers.Add($wdAlignPageNumberCenter, $true)
    }

    foreach($toc in $doc.TablesOfContents){ $toc.Update() }
    $doc.Fields.Update() | Out-Null
    $doc.Save()
    'FINAL_WORD_DOC_OK'
} catch {
    'FINAL_WORD_DOC_FAIL'
    $_.Exception.Message
} finally {
    if($doc){ try { $doc.Close() } catch {} }
    if($word){ try { $word.Quit() } catch {} }
}
