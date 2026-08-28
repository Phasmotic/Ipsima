import Foundation
@testable import Talaria
import XCTest

final class LaunchActivityAuditTests: XCTestCase {
    func testFirstFrameWithoutTalariaOwnedConstructionIsClear() {
        let audit = LaunchActivityAudit()

        let snapshot = audit.recordFirstFrame()

        XCTAssertTrue(snapshot.firstFrameRecorded)
        XCTAssertTrue(snapshot.records.isEmpty)
        XCTAssertTrue(snapshot.isClearAtFirstFrame)
    }

    func testConstructionBeforeFirstFrameIsRetainedAsViolation() {
        let audit = LaunchActivityAudit()
        audit.recordTalariaOwnedConstruction(.urlSession)

        let snapshot = audit.recordFirstFrame()

        XCTAssertEqual(snapshot.resourcesConstructedBeforeFirstFrame, [.urlSession])
        XCTAssertFalse(snapshot.isClearAtFirstFrame)
    }

    func testConstructionAfterFirstFrameDoesNotRewriteLaunchHistory() {
        let audit = LaunchActivityAudit()
        audit.recordFirstFrame()
        audit.recordTalariaOwnedConstruction(.timer)

        let snapshot = audit.snapshot()

        XCTAssertEqual(
            snapshot.records,
            [LaunchActivityRecord(resource: .timer, occurredBeforeFirstFrame: false)]
        )
        XCTAssertTrue(snapshot.isClearAtFirstFrame)
    }

    func testRepeatedFirstFrameSignalIsIdempotent() {
        let audit = LaunchActivityAudit()
        audit.recordFirstFrame()
        audit.recordTalariaOwnedConstruction(.reachability)

        let repeatedSnapshot = audit.recordFirstFrame()

        XCTAssertEqual(
            repeatedSnapshot.records,
            [LaunchActivityRecord(resource: .reachability, occurredBeforeFirstFrame: false)]
        )
        XCTAssertTrue(repeatedSnapshot.isClearAtFirstFrame)
    }

    func testConcurrentConstructionRecordingIsLossless() {
        let audit = LaunchActivityAudit()
        let expectedCount = 128

        DispatchQueue.concurrentPerform(iterations: expectedCount) { _ in
            audit.recordTalariaOwnedConstruction(.dispatchTimer)
        }

        let snapshot = audit.snapshot()
        XCTAssertEqual(snapshot.records.count, expectedCount)
        XCTAssertEqual(snapshot.resourcesConstructedBeforeFirstFrame.count, expectedCount)
    }

    func testAuditedFactoryRecordsURLSessionConstruction() {
        let audit = LaunchActivityAudit.shared
        let before = audit.snapshot()

        let session = AuditedLaunchResourceFactory.urlSession(configuration: .ephemeral)
        session.invalidateAndCancel()

        let after = audit.snapshot()
        XCTAssertEqual(after.records.count, before.records.count + 1)
        XCTAssertEqual(after.records.last?.resource, .urlSession)
        XCTAssertEqual(
            after.records.last?.occurredBeforeFirstFrame,
            !before.firstFrameRecorded
        )
    }
}
