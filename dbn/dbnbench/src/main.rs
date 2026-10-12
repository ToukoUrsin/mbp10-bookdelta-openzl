//! DBN side of the MBP-10 comparison, using only the official `dbn` crate.
//!   from-csv SRC.csv.zst OUT.dbn  - rebuild DBN MBP-10 records from Databento's raw-integer CSV
//!   to-csv IN.dbn                 - official CSV encoder (with mapped symbol) to stdout
//!   zst IN.dbn OUT.dbn.zst        - re-encode with the dbn crate's own zstd writer (time it)
//!   decode IN.dbn[.zst]           - official decoder, every record touched; prints JSON
use dbn::{
    decode::{DbnMetadata, DecodeRecord, DynDecoder},
    encode::{CsvEncoder, DbnEncoder, DynWriter, EncodeRecord, EncodeRecordTextExt},
    BidAskPair, Compression, Dataset, MappingInterval, Mbp10Msg, Metadata, RecordHeader, SType,
    Schema, SymbolIndex, SymbolMapping, TsSymbolMap, VersionUpgradePolicy,
};
use std::{
    collections::BTreeMap,
    fs::File,
    io::{self, BufRead, BufReader, BufWriter, Write},
    time::Instant,
};

const HEADER: &str = "ts_recv,ts_event,rtype,publisher_id,instrument_id,action,side,depth,price,size,flags,ts_in_delta,sequence,bid_px_00,ask_px_00,bid_sz_00,ask_sz_00,bid_ct_00,ask_ct_00,bid_px_01,ask_px_01,bid_sz_01,ask_sz_01,bid_ct_01,ask_ct_01,bid_px_02,ask_px_02,bid_sz_02,ask_sz_02,bid_ct_02,ask_ct_02,bid_px_03,ask_px_03,bid_sz_03,ask_sz_03,bid_ct_03,ask_ct_03,bid_px_04,ask_px_04,bid_sz_04,ask_sz_04,bid_ct_04,ask_ct_04,bid_px_05,ask_px_05,bid_sz_05,ask_sz_05,bid_ct_05,ask_ct_05,bid_px_06,ask_px_06,bid_sz_06,ask_sz_06,bid_ct_06,ask_ct_06,bid_px_07,ask_px_07,bid_sz_07,ask_sz_07,bid_ct_07,ask_ct_07,bid_px_08,ask_px_08,bid_sz_08,ask_sz_08,bid_ct_08,ask_ct_08,bid_px_09,ask_px_09,bid_sz_09,ask_sz_09,bid_ct_09,ask_ct_09,symbol";

fn csv_lines(path: &str) -> impl Iterator<Item = String> {
    let mut d = zstd::stream::read::Decoder::new(File::open(path).unwrap()).unwrap();
    d.window_log_max(31).unwrap();
    let mut lines = BufReader::with_capacity(1 << 22, d).lines().map(|l| l.unwrap());
    assert_eq!(lines.next().unwrap(), HEADER, "unexpected CSV header");
    lines
}

fn parse(line: &str) -> (Mbp10Msg, &str) {
    let f: Vec<&str> = line.split(',').collect();
    assert_eq!(f.len(), 74);
    let i = |k: usize| -> i64 { f[k].parse::<i64>().unwrap() };
    let u = |k: usize| -> u64 { f[k].parse::<u64>().unwrap() };
    let c = |k: usize| -> std::os::raw::c_char {
        let b = f[k].as_bytes();
        assert_eq!(b.len(), 1);
        b[0] as std::os::raw::c_char
    };
    let mut m = Mbp10Msg::default();
    m.hd = RecordHeader::new::<Mbp10Msg>(u(2) as u8, u(3) as u16, u(4) as u32, u(1));
    m.ts_recv = u(0);
    m.action = c(5);
    m.side = c(6);
    m.depth = u(7) as u8;
    m.price = i(8);
    m.size = u(9) as u32;
    m.flags = (u(10) as u8).into();
    m.ts_in_delta = i(11) as i32;
    m.sequence = u(12) as u32;
    for l in 0..10 {
        let b = 13 + 6 * l;
        m.levels[l] = BidAskPair {
            bid_px: i(b),
            ask_px: i(b + 1),
            bid_sz: u(b + 2) as u32,
            ask_sz: u(b + 3) as u32,
            bid_ct: u(b + 4) as u32,
            ask_ct: u(b + 5) as u32,
        };
    }
    (m, f[73])
}

fn from_csv(src: &str, out: &str) {
    // Pass 1: symbology and time range, so the metadata header can be written first.
    let (mut syms, mut t0, mut t1) = (BTreeMap::<String, u32>::new(), u64::MAX, 0u64);
    let mut rows = 0u64;
    for line in csv_lines(src) {
        let mut it = line.rsplitn(2, ',');
        let sym = it.next().unwrap().to_string();
        let mut f = it.next().unwrap().splitn(6, ',');
        let ts: u64 = f.next().unwrap().parse().unwrap();
        let iid: u32 = f.nth(3).unwrap().parse().unwrap();
        if let Some(prev) = syms.insert(sym.clone(), iid) {
            assert_eq!(prev, iid, "symbol {sym} maps to two instrument ids");
        }
        t0 = t0.min(ts);
        t1 = t1.max(ts);
        rows += 1;
    }
    let day = 86_400_000_000_000u64;
    let (start, end) = (t0 / day * day, (t1 / day + 1) * day);
    let d0 = time::OffsetDateTime::from_unix_timestamp_nanos(start as i128).unwrap().date();
    let d1 = time::OffsetDateTime::from_unix_timestamp_nanos(end as i128).unwrap().date();
    let mappings = syms
        .iter()
        .map(|(s, id)| SymbolMapping {
            raw_symbol: s.clone(),
            intervals: vec![MappingInterval { start_date: d0, end_date: d1, symbol: id.to_string() }],
        })
        .collect();
    let meta = Metadata::builder()
        .dataset(Dataset::GlbxMdp3)
        .schema(Some(Schema::Mbp10))
        .start(start)
        .end(std::num::NonZeroU64::new(end))
        .stype_in(Some(SType::RawSymbol))
        .stype_out(SType::InstrumentId)
        .symbols(syms.keys().cloned().collect())
        .mappings(mappings)
        .build();
    // Pass 2: records.
    let mut enc = DbnEncoder::new(BufWriter::with_capacity(1 << 22, File::create(out).unwrap()), &meta).unwrap();
    let mut n = 0u64;
    for line in csv_lines(src) {
        let (m, _) = parse(&line);
        enc.encode_record(&m).unwrap();
        n += 1;
    }
    enc.get_mut().flush().unwrap();
    assert_eq!(n, rows);
    eprintln!("{{\"rows\":{n},\"symbols\":{},\"start\":{start},\"end\":{end}}}", syms.len());
}

fn to_csv(inp: &str) {
    let mut dec = DynDecoder::from_file(inp, VersionUpgradePolicy::AsIs).unwrap();
    let map = TsSymbolMap::from_metadata(dec.metadata()).unwrap();
    let out = BufWriter::with_capacity(1 << 22, io::stdout().lock());
    let mut enc = CsvEncoder::builder(out).use_pretty_px(false).use_pretty_ts(false)
        .write_header(true).schema(Some(Schema::Mbp10)).with_symbol(true).build().unwrap();
    while let Some(r) = dec.decode_record::<Mbp10Msg>().unwrap() {
        let s = map.get_for_rec(r).map(|s| s.as_str());
        enc.encode_record_with_sym(r, s).unwrap();
    }
    enc.flush().unwrap();
}

fn zst(inp: &str, out: &str) {
    let t = Instant::now();
    let mut dec = DynDecoder::from_file(inp, VersionUpgradePolicy::AsIs).unwrap();
    let meta = dec.metadata().clone();
    let w = DynWriter::new(BufWriter::with_capacity(1 << 22, File::create(out).unwrap()), Compression::Zstd).unwrap();
    let mut enc = DbnEncoder::new(w, &meta).unwrap();
    let mut n = 0u64;
    while let Some(r) = dec.decode_record::<Mbp10Msg>().unwrap() {
        enc.encode_record(r).unwrap();
        n += 1;
    }
    drop(enc);
    let s = t.elapsed().as_secs_f64();
    println!("{{\"op\":\"dbn-zst-encode\",\"rows\":{n},\"seconds\":{s:.4},\"bytes\":{}}}", std::fs::metadata(out).unwrap().len());
}

fn decode(inp: &str) {
    let t = Instant::now();
    let mut dec = DynDecoder::from_file(inp, VersionUpgradePolicy::AsIs).unwrap();
    let (mut n, mut sum) = (0u64, 0u64);
    while let Some(r) = dec.decode_record::<Mbp10Msg>().unwrap() {
        // Touch every byte of the record: wrapping sum of its 46 u64 words.
        let words: &[u64; 46] = unsafe { &*(r as *const Mbp10Msg as *const [u64; 46]) };
        for w in words {
            sum = sum.wrapping_add(*w);
        }
        n += 1;
    }
    let s = t.elapsed().as_secs_f64();
    println!("{{\"op\":\"dbn-decode\",\"file\":\"{inp}\",\"rows\":{n},\"seconds\":{s:.4},\"checksum\":\"{sum:016x}\"}}");
}

fn main() {
    assert_eq!(std::mem::size_of::<Mbp10Msg>(), 368);
    let a: Vec<String> = std::env::args().collect();
    match a[1].as_str() {
        "from-csv" => from_csv(&a[2], &a[3]),
        "to-csv" => to_csv(&a[2]),
        "zst" => zst(&a[2], &a[3]),
        "decode" => decode(&a[2]),
        _ => panic!("usage: from-csv|to-csv|zst|decode"),
    }
}
