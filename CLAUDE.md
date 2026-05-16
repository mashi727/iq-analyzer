# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Conversation Guidelines
- あなたは世界最高の信号処理技術者であり、Pythonプログラマーです。

## Project Overview

This is a high-performance IQ data viewer and processing tool for Rohde & Schwarz IQW measurement data. The application must handle extremely large datasets (up to 20GB) efficiently on resource-constrained hardware (Core i3, 8GB RAM, Windows 11).

## Target Specifications

### Hardware Constraints
- **Target Machine**: Windows 11, Intel Core i3, 8GB RAM
- **Data Size**: Up to 20GB of IQ data per file
- **Deployment**: Single-file executable (EXE)
- **Critical Requirement**: No stack overflow or memory errors under any circumstances

### Software Stack
- **Primary Language**: Python 3
- **GUI Framework**: PySide6
- **Plotting Library**: PyQtGraph
- **Optional Performance**: Cython or Julia (if single-file EXE is achievable)
- **Theme**: qdarktheme (optional, fallback to default)

## Data Formats: Rohde & Schwarz IQ Files

The application supports two R&S IQ data formats:

### 1. WVH/WVD Format (Legacy IQW)

**File Types**:
1. **WVH (Header)**: XML-like text format containing metadata
2. **WVD (Data)**: Binary IQ data in RAW16LE format

**WVH Header Structure**:
```
{COPYRIGHT:2025 Rohde&Schwarz IQW}
{FWVERSION:3.2.3}
{DATE:2025-01-23;00:58:58}
{TYPE:RAW16LE}
{COMPONENTS:IQ}
{CLOCK:32000000.000000}
{CHANNAME0:FSW-43 102905 DIG IQ OUT CH0}
{RESOLUTION:16}
{FREQUENCY:0.000000}
{REFLEVEL:0}
{SAMPLES:2837446656}
```

**Key Parameters**:
- `TYPE`: Data encoding format (typically RAW16LE = 16-bit little-endian)
- `COMPONENTS`: IQ (In-phase/Quadrature components)
- `CLOCK`: Sampling rate in Hz
- `SAMPLES`: Total number of IQ samples
- `RESOLUTION`: Bits per sample
- `REFLEVEL`: Reference level in dBm (floating point)

**Data Reading Strategy**:
- Parse WVH for metadata
- Memory-map WVD for efficient large file handling
- Use numpy memory mapping (`np.memmap`) to avoid loading entire file into RAM

### 2. iq.tar Format (Modern IQ-TAR)

**File Structure**:
- Single `.iq.tar` archive containing:
  1. **XML Metadata File** (`*.xml`): Contains comprehensive IQ parameters
  2. **Binary Data File** (`*.complex*.float32`): IQ data in float32 format
  3. **XSLT Stylesheet** (optional): For web browser preview

**XML Metadata Structure**:
```xml
<RS_IQ_TAR_FileFormat fileFormatVersion="3">
  <Name>FSW-43</Name>
  <DateTime>2017-10-25 23:42:23</DateTime>
  <Samples>250000000</Samples>
  <Clock unit="Hz">2500000000.000000</Clock>
  <Format>complex</Format>
  <DataType>float32</DataType>
  <DataFilename>File_2017-10-25234347.complex1ch.float32</DataFilename>
  <UserData>
    <RohdeSchwarz>
      <DataImportExport_MandatoryData>
        <CenterFrequency unit="Hz">9000000000.000000</CenterFrequency>
      </DataImportExport_MandatoryData>
      <DataImportExport_OptionalData>
        <Key name="Ch1_RefLevel[dBm]">0</Key>
      </DataImportExport_OptionalData>
    </RohdeSchwarz>
  </UserData>
</RS_IQ_TAR_FileFormat>
```

**Key Metadata Elements**:
- `Samples`: Total number of IQ samples
- `Clock`: Sampling rate in Hz
- `Format`: Data format (complex, real, or polar)
- `DataType`: Bit precision (float32 or float64)
- `CenterFrequency`: Modulation center frequency in Hz
- `Ch1_RefLevel[dBm]`: Reference level in dBm

**Data Reading Strategy**:
1. Extract tar archive to temporary directory
2. Parse XML metadata to locate binary data file
3. Memory-map binary data file (float32 interleaved I/Q)
4. Clean up temporary directory on close

**Format Conversion**:
- When saving iq.tar data to WVH/WVD format:
  - Convert float32 to int16 (RAW16LE)
  - Normalize to prevent clipping
  - Preserve metadata (sample rate, center frequency, etc.)

## Application Architecture

### UI Layout (Top to Bottom)

```
┌─────────────────────────────────────────────────┐
│  Spectrogram (Region範囲)                       │
│  - ROI selection enabled                        │
│  - Frequency vs Time                            │
└─────────────────────────────────────────────────┘
┌──────────────────────────┬──────────────────────┐
│  Detailed Time-Amplitude │  Control Panel       │
│  (Region範囲、間引きなし)  │  - Open/Save buttons │
│                          │  - Normalization     │
│                          │  - Colormap controls │
│                          │  - Threshold slider  │
└──────────────────────────┴──────────────────────┘
┌─────────────────────────────────────────────────┐
│  Overview Time-Amplitude (全体、間引きあり)      │
│  - Region selector (pyqtgraph LinearRegionItem) │
│  - Normalized amplitude (default 90%)           │
└─────────────────────────────────────────────────┘
```

### Key Components

#### 1. CustomSpectrogramWidget
- Displays 2D spectrogram with axis labels
- Uses `pg.ImageItem` with `pg.HistogramLUTItem` for colormap
- ROI (Region of Interest) selection with `pg.RectROI`
- Independent zoom capability
- Colormap options: plasma (default), viridis, inferno, magma

#### 2. RSIQViewer (Main Window)
- Manages all plot widgets and controls
- Handles both WVH/WVD and iq.tar file loading
- Automatic format detection and loader selection
- Implements intelligent decimation strategy
- Provides region-based detail viewing
- Memory-efficient sequential file opening with cleanup

#### 3. File Loaders

**WVFileLoader**:
- Parses WVH header files
- Memory-maps WVD binary data (int16 format)
- Provides get_iq_data() for range-based access
- Safe cleanup with explicit memmap closure

**IQTarLoader**:
- Extracts and parses iq.tar archives
- Parses XML metadata
- Memory-maps float32 binary data
- Automatic temporary directory cleanup
- Compatible with WVFileLoader interface

## Memory Optimization Strategies

### Critical Implementation Requirements

1. **Memory Mapping**
   ```python
   # Use memmap instead of loading entire file
   data = np.memmap(wvd_path, dtype=np.int16, mode='r')
   ```

2. **Intelligent Decimation**
   - **Overview Display**: Decimate based on screen resolution
   - **Threshold Preservation**: Signals above threshold are never decimated
   - **Adaptive Downsampling**: Adjust decimation factor based on data size and display width
   - **Default Amplitude Normalization**: 90% (adjustable)

3. **Progressive Loading**
   - Load only visible region for spectrogram calculation
   - Update on ROI change
   - Chunk processing for large datasets

4. **Data Type Optimization**
   - Use appropriate dtypes (int16 for raw data, float32 for processing)
   - Avoid unnecessary copies

## Development Guidelines

### Code Documentation
- **Critical**: Include detailed comments explaining WHY specific coding decisions were made
- Document memory optimization strategies
- Explain decimation algorithms
- Note performance trade-offs

### Error Handling
- Robust file format validation
- Graceful degradation on memory pressure
- User-friendly error messages
- No silent failures

### Testing Considerations
- Test with full 20GB dataset
- Verify memory usage stays within 8GB limit
- Check for memory leaks during repeated ROI changes
- Profile performance on target hardware spec

## Common Development Tasks

### Running the Application
```bash
# Main application
python rs_iq_viewer.py

# Test iq.tar loader
python test_iqtar.py
```

### Building Single-File Executable

**Windows 11の場合**: `BUILD_INSTRUCTIONS.md` を参照

**簡単な方法**:
```cmd
build_windows.bat
```

**手動ビルド**:
```bash
# Using PyInstaller (recommended)
pyinstaller --onefile --windowed --name="RS_IQ_Viewer" rs_iq_viewer.py

# Using spec file (advanced)
pyinstaller rs_iq_viewer.spec

# Alternative: Using Nuitka (better performance)
nuitka --standalone --onefile --windows-disable-console --enable-plugin=pyside6 rs_iq_viewer.py
```

**出力**: `dist/RS_IQ_Viewer.exe` (80-120MB)

### Required Dependencies
```
PySide6
pyqtgraph
numpy
scipy
qdarktheme (optional)
```

## Reference Implementation

The `rs_iq_viewer.py` file is the main implementation with:
- Dual-format support (WVH/WVD and iq.tar)
- Memory-efficient data loading with memmap
- ROI-based spectrogram calculation
- Multi-plot coordination
- Colormap management
- Sequential file opening with proper cleanup

**Key Implementation Files**:
- `rs_iq_viewer.py`: Main viewer application (lines 1-1700+)
  - `WVFileLoader` class (lines 35-402): WVH/WVD format handler
  - `IQTarLoader` class (lines 404-703): iq.tar format handler
  - `RSIQViewer` class (lines 858+): Main GUI window
- `test_iqtar.py`: Unit tests for iq.tar loader

Study these sections for implementation patterns:
- **Memory mapping**: Search for `np.memmap` usage in both loaders
- **Format detection**: `open_file()` method (lines 1200+)
- **Data conversion**: `save_region()` method for float32→int16 conversion
- **Cleanup strategy**: `cleanup_previous_data()` method (lines 1124-1198)
- **XML parsing**: `IQTarLoader.parse_iqtar()` method (lines 425-554)

## Performance Targets

- **Startup Time**: < 5 seconds for 20GB file (metadata only)
- **ROI Update**: < 2 seconds for spectrogram recalculation
- **Memory Footprint**: < 2.5GB peak usage (safe for 8GB RAM systems)
- **Responsiveness**: UI remains interactive during all operations

### Memory Usage Verification (Windows 11, Core i3, 8GB RAM)

**Tested Scenarios**:
1. **WVD Medium File** (65M samples, 250MB): ~450 MB RAM
2. **iq.tar Large File** (250M samples, 2GB): ~680 MB RAM
3. **Worst Case** (50% Region selection): ~2.5 GB RAM

**Safety Features**:
- Memory usage warning when estimated > 2GB
- User confirmation before processing large regions
- Automatic memory cleanup between file loads
- numpy memmap for zero-copy file access

## Notes

- Always prioritize memory efficiency over speed
- Test on actual target hardware before deployment
- Consider progressive rendering for very large spectrograms
- Use Qt threading (`QThread`) for long-running operations to maintain UI responsiveness

### Format-Specific Considerations

**WVH/WVD Format**:
- Data is int16, efficient for storage but limited dynamic range
- May have header-data mismatches; auto-correction implemented
- Original format preserves exact bit-level representation

**iq.tar Format**:
- Data is float32, higher precision and dynamic range
- Larger file sizes (2x compared to int16)
- Requires temporary directory for extraction
- When converting to WVH/WVD, normalization is applied to prevent clipping
- Temporary files are automatically cleaned up on application close

### Sequential File Opening

When opening multiple large files in sequence:
1. Previous file's memmap is explicitly closed
2. All plot data is cleared
3. Qt event processing ensures UI updates
4. Garbage collection is forced
5. Internal state is reset

This prevents memory accumulation and ensures stable operation even with 20GB+ files.
