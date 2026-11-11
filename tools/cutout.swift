// Cuts the foreground (a cup, a tin, ...) out of a studio photo using Apple's Vision framework.
// Usage:  swift tools/cutout.swift <sheet.png> <cols> <rows> <count> <out-prefix> <max-height-px>
// A single photo is  `<img> 1 1 1 out 900`  ->  out-1.png (transparent PNG).
// A catalogue sheet (e.g. 4 x 3 cups) is cut cell by cell, left to right, top to bottom -> out-1.png, out-2.png ...
// Needs macOS 14+ (VNGenerateForegroundInstanceMaskRequest).
import Vision
import CoreImage
import ImageIO
import Foundation

let a = CommandLine.arguments
guard a.count == 7 else { print("usage: cutout.swift sheet cols rows count out-prefix max-height"); exit(1) }
let src = CGImageSourceCreateWithURL(URL(fileURLWithPath: a[1]) as CFURL, nil)!
let img = CGImageSourceCreateImageAtIndex(src, 0, nil)!
let cols = Int(a[2])!, rows = Int(a[3])!, count = Int(a[4])!
let prefix = a[5], maxH = CGFloat(Double(a[6])!)
let cw = img.width / cols, ch = img.height / rows
let ctx = CIContext(), cs = CGColorSpace(name: CGColorSpace.sRGB)!
for i in 0..<count {
    let r = i / cols, c = i % cols
    guard let cell = img.cropping(to: CGRect(x: c * cw, y: r * ch, width: cw, height: ch)) else { continue }
    let handler = VNImageRequestHandler(cgImage: cell)
    let req = VNGenerateForegroundInstanceMaskRequest()
    do {
        try handler.perform([req])
        guard let obs = req.results?.first else { print("no subject in cell", i + 1); continue }
        let buf = try obs.generateMaskedImage(ofInstances: obs.allInstances, from: handler, croppedToInstancesExtent: true)
        var ci = CIImage(cvPixelBuffer: buf)
        let s = min(1, maxH / ci.extent.height)
        ci = ci.transformed(by: CGAffineTransform(scaleX: s, y: s))
        try ctx.pngRepresentation(of: ci, format: .RGBA8, colorSpace: cs)!.write(to: URL(fileURLWithPath: "\(prefix)-\(i + 1).png"))
        print("wrote", "\(prefix)-\(i + 1).png")
    } catch { print("error in cell", i + 1, error) }
}
