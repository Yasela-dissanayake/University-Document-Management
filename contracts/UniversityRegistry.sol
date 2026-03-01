// SPDX-License-Identifier: MIT
pragma solidity =0.8.28;

/**
 * @title  UniversityRegistry
 * @notice Immutable ledger of student semester records and University letters.
 *         Access control: only the contract owner (deployer) OR explicitly
 *         allow-listed callers (e.g. the backend service wallet) may write.
 *
 * @dev    Audit fix (2026-03-01):
 *         - Replaced no-op `onlyAuthorized` modifier with real owner / caller
 *           allowlist pattern.
 *         - Added `year` range validation (1–10 to cover under/postgrad).
 *         - Added SPDX license and pinned pragma.
 */
contract UniversityRegistry {

    // ===== Access Control =====

    address public owner;

    /// @dev Addresses allowed to call write functions (e.g. backend service wallet)
    mapping(address => bool) public allowedCallers;

    event CallerAllowed(address indexed caller, bool allowed);
    event OwnershipTransferred(address indexed previousOwner, address indexed newOwner);

    constructor() {
        owner = msg.sender;
        allowedCallers[msg.sender] = true;
    }

    modifier onlyOwner() {
        require(msg.sender == owner, "UniversityRegistry: caller is not owner");
        _;
    }

    modifier onlyAuthorized() {
        require(
            msg.sender == owner || allowedCallers[msg.sender],
            "UniversityRegistry: caller is not authorized"
        );
        _;
    }

    /// @notice Grant or revoke write access for a caller address.
    function setCallerAllowed(address caller, bool allowed) external onlyOwner {
        allowedCallers[caller] = allowed;
        emit CallerAllowed(caller, allowed);
    }

    /// @notice Transfer ownership to a new address.
    function transferOwnership(address newOwner) external onlyOwner {
        require(newOwner != address(0), "UniversityRegistry: zero address");
        emit OwnershipTransferred(owner, newOwner);
        owner = newOwner;
        allowedCallers[newOwner] = true;
    }

    // ===== Data Structures =====

    struct StudentRecord {
        string studentId;
        string name;
        string program;
        uint8 year;
        string documentsIPFSHash; // Latest document CID (quick access)
        bytes32 contentHash;      // Latest content hash (quick verify)
        uint256 timestamp;        // Latest record timestamp
        bool isActive;
        address studentWallet;
        string[] semesterHashes;   // VERSIONED: all transcript/doc hashes
        bytes32[] contentHashes;   // VERSIONED: all content hashes
        uint256[] timestamps;      // VERSIONED: one per semester append
    }

    struct DocumentNode {
        string prev;
        string next;
        bool exists;
    }

    mapping(string => StudentRecord) public students;
    mapping(string => DocumentNode) private studentLinks;
    string[] public studentIds;
    string public head;
    string public tail;
    uint256 public studentCount;

    // ===== Events =====

    event StudentRegistered(
        string indexed studentId,
        string name,
        string program,
        string documentsIPFSHash
    );
    event DocumentUpdated(
        string indexed studentId,
        string oldIPFSHash,
        string newIPFSHash,
        address updatedBy
    );
    event DocumentVerified(
        string indexed studentId,
        string ipfsHash,
        address verifiedBy,
        uint256 timestamp
    );

    // ===== Modifiers =====

    modifier studentExists(string memory _studentId) {
        require(
            bytes(students[_studentId].studentId).length > 0,
            "UniversityRegistry: student not found"
        );
        _;
    }

    // ===== Core Write Functions =====

    /**
     * @notice Register a new student with their first semester record.
     * @param _year Academic year, must be between 1 and 10.
     */
    function registerStudent(
        string memory _studentId,
        string memory _name,
        string memory _program,
        uint8 _year,
        string memory _documentsIPFSHash,
        bytes32 _contentHash,
        address _studentWallet
    ) public onlyAuthorized {
        require(
            bytes(students[_studentId].studentId).length == 0,
            "UniversityRegistry: student already registered"
        );
        require(_year >= 1 && _year <= 10, "UniversityRegistry: year must be 1-10");
        require(bytes(_studentId).length > 0, "UniversityRegistry: empty student ID");

        StudentRecord storage s = students[_studentId];
        s.studentId = _studentId;
        s.name = _name;
        s.program = _program;
        s.year = _year;
        s.documentsIPFSHash = _documentsIPFSHash;
        s.contentHash = _contentHash;
        s.timestamp = block.timestamp;
        s.isActive = true;
        s.studentWallet = _studentWallet;

        // Initialize versioning arrays
        s.semesterHashes.push(_documentsIPFSHash);
        s.contentHashes.push(_contentHash);
        s.timestamps.push(block.timestamp);

        studentIds.push(_studentId);

        // Maintain doubly-linked list for enumeration
        if (studentCount == 0) {
            head = _studentId;
            tail = _studentId;
        } else {
            studentLinks[tail].next = _studentId;
            studentLinks[_studentId].prev = tail;
            tail = _studentId;
        }
        studentLinks[_studentId].exists = true;
        studentCount++;

        emit StudentRegistered(_studentId, _name, _program, _documentsIPFSHash);
    }

    /**
     * @notice Append a new semester record for an existing student.
     * @dev    Only authorized callers (backend service wallet) may append records.
     *         Off-chain workflow approval must be completed before this is called.
     */
    function addSemesterRecord(
        string memory _studentId,
        string memory _ipfsHash,
        bytes32 _contentHash
    ) public onlyAuthorized studentExists(_studentId) {
        require(bytes(_ipfsHash).length > 0, "UniversityRegistry: empty IPFS hash");

        StudentRecord storage s = students[_studentId];
        string memory oldHash = s.documentsIPFSHash;

        s.semesterHashes.push(_ipfsHash);
        s.contentHashes.push(_contentHash);
        s.timestamps.push(block.timestamp);

        // Update "current/latest" reference fields for quick access
        s.documentsIPFSHash = _ipfsHash;
        s.contentHash = _contentHash;
        s.timestamp = block.timestamp;

        emit DocumentUpdated(_studentId, oldHash, _ipfsHash, msg.sender);
    }

    // ===== View / Getters =====

    function getStudentDetails(
        string memory _studentId
    )
        public
        view
        studentExists(_studentId)
        returns (
            string memory studentId,
            string memory name,
            string memory program,
            uint8 year,
            string memory documentsIPFSHash,
            bytes32 contentHash,
            uint256 timestamp,
            bool isActive
        )
    {
        StudentRecord memory s = students[_studentId];
        return (
            s.studentId,
            s.name,
            s.program,
            s.year,
            s.documentsIPFSHash,
            s.contentHash,
            s.timestamp,
            s.isActive
        );
    }

    /// @notice Return all semester IPFS hashes for a student.
    function getStudentSemesterHashes(
        string memory _studentId
    ) public view studentExists(_studentId) returns (string[] memory) {
        return students[_studentId].semesterHashes;
    }

    /// @notice Return all content hashes (one per semester) for a student.
    function getStudentContentHashes(
        string memory _studentId
    ) public view studentExists(_studentId) returns (bytes32[] memory) {
        return students[_studentId].contentHashes;
    }

    /// @notice Return all timestamps (one per semester) for a student.
    function getStudentTimestamps(
        string memory _studentId
    ) public view studentExists(_studentId) returns (uint256[] memory) {
        return students[_studentId].timestamps;
    }

    function getStudentCount() public view returns (uint256) {
        return studentCount;
    }

    function getAllStudentIds() public view returns (string[] memory) {
        return studentIds;
    }

    function studentRecordExists(
        string memory _studentId
    ) public view returns (bool) {
        return bytes(students[_studentId].studentId).length > 0;
    }
}
