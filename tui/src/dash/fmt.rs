//! Numbers as the web page writes them (app.js fmtCost, fmtTokens, fmtMs).

pub fn cost(c: f64) -> String {
    if c == 0.0 {
        "$0".into()
    } else if c < 0.01 {
        format!("${c:.4}")
    } else if c < 1.0 {
        format!("${c:.3}")
    } else {
        format!("${c:.2}")
    }
}

pub fn tokens(n: f64) -> String {
    if n >= 1e6 {
        if n >= 1e7 {
            format!("{:.0}M", n / 1e6)
        } else {
            format!("{:.1}M", n / 1e6)
        }
    } else if n >= 1e3 {
        if n >= 1e4 {
            format!("{:.0}k", n / 1e3)
        } else {
            format!("{:.1}k", n / 1e3)
        }
    } else {
        format!("{n:.0}")
    }
}

pub fn ms(ms: f64) -> String {
    if ms >= 1000.0 {
        if ms >= 10000.0 {
            format!("{:.0}s", ms / 1000.0)
        } else {
            format!("{:.1}s", ms / 1000.0)
        }
    } else {
        format!("{ms:.0}ms")
    }
}

pub fn percent(r: f64) -> String {
    format!("{:.0}%", r * 100.0)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn as_the_web_page() {
        assert_eq!(
            (cost(0.0), cost(0.0049), cost(0.052), cost(3.5)),
            ("$0".into(), "$0.0049".into(), "$0.052".into(), "$3.50".into())
        );
        assert_eq!(
            (tokens(512.0), tokens(35683.0), tokens(1500.0), tokens(2_500_000.0)),
            ("512".into(), "36k".into(), "1.5k".into(), "2.5M".into())
        );
        assert_eq!(
            (ms(440.5), ms(2960.0), ms(17745.0)),
            ("440ms".into(), "3.0s".into(), "18s".into())
        );
    }
}
